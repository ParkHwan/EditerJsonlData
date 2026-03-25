"""파일 서비스 (Phase 2: Line Indexer + Atomic Write + Backup)

대용량 JSONL 파일을 효율적으로 처리하기 위해
Line Index(바이트 오프셋 맵)를 구축하여 Random Access를 지원한다.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
import aiofiles.os
from fastapi import HTTPException

from app.core.config import settings
from app.core.logger import logger
from app.schemas.item import ItemBase


class LineIndex:
    """JSONL 파일의 각 라인 바이트 오프셋을 관리하는 인덱서

    구조: offsets[i] = i번째 라인의 시작 바이트 위치
    """

    def __init__(self) -> None:
        self.offsets: list[int] = []
        self.total_lines: int = 0
        self.file_mtime: float = 0.0

    def is_stale(self, file_path: Path) -> bool:
        """인덱스가 파일보다 오래되었는지 확인"""
        if not file_path.exists():
            return True
        return file_path.stat().st_mtime != self.file_mtime

    async def build(self, file_path: Path) -> None:
        """파일을 스캔하여 라인별 바이트 오프셋 인덱스 구축"""
        offsets: list[int] = []
        async with aiofiles.open(file_path, mode="rb") as f:
            offset = 0
            while True:
                line = await f.readline()
                if not line:
                    break
                stripped = line.strip()
                if stripped:
                    offsets.append(offset)
                offset += len(line)

        self.offsets = offsets
        self.total_lines = len(offsets)
        self.file_mtime = file_path.stat().st_mtime
        logger.info(
            f"Index built: {file_path.name} → {self.total_lines} lines"
        )

    def invalidate(self) -> None:
        """인덱스 무효화 (파일 수정 후)"""
        self.offsets = []
        self.total_lines = 0
        self.file_mtime = 0.0


class FileService:
    """JSONL 파일 I/O 서비스

    - Line Index 기반 Random Access (대용량 지원)
    - Optimistic Locking (버전 체크)
    - Atomic Write (임시 파일 → os.replace)
    - Auto Backup (저장 전 자동 백업)
    """

    def __init__(self) -> None:
        self.data_dir = Path(settings.DATA_DIR)
        self.backup_dir = Path(settings.BACKUP_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        # file_id → LineIndex 캐시
        self._index_cache: dict[str, LineIndex] = {}

    # ------------------------------------------------------------------
    # 경로 유틸리티
    # ------------------------------------------------------------------
    def _get_file_path(self, file_id: str) -> Path:
        """디렉터리 순회 방지 + 파일 경로 반환"""
        safe_id = Path(file_id).name
        return self.data_dir / f"{safe_id}.jsonl"

    # ------------------------------------------------------------------
    # Line Index
    # ------------------------------------------------------------------
    async def _get_index(self, file_id: str) -> LineIndex:
        """인덱스 반환 (필요 시 구축/갱신)"""
        file_path = self._get_file_path(file_id)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        index = self._index_cache.get(file_id)
        if index is None or index.is_stale(file_path):
            index = LineIndex()
            await index.build(file_path)
            self._index_cache[file_id] = index

        return index

    def _invalidate_index(self, file_id: str) -> None:
        """파일 변경 후 인덱스 무효화"""
        if file_id in self._index_cache:
            self._index_cache[file_id].invalidate()
            del self._index_cache[file_id]

    # ------------------------------------------------------------------
    # 파일 목록
    # ------------------------------------------------------------------
    def list_files(self) -> list[dict[str, Any]]:
        """data/ 디렉터리의 JSONL 파일 목록 반환"""
        files: list[dict[str, Any]] = []
        for f in sorted(self.data_dir.glob("*.jsonl")):
            stat = f.stat()
            files.append(
                {
                    "id": f.stem,
                    "name": f.name,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return files

    # ------------------------------------------------------------------
    # Row 읽기 (Random Access)
    # ------------------------------------------------------------------
    async def get_total_rows(self, file_id: str) -> int:
        """파일의 전체 Row 수 반환"""
        index = await self._get_index(file_id)
        return index.total_lines

    async def get_data_id_list(self, file_id: str) -> list[dict[str, Any]]:
        """파일의 모든 Row에서 data_id만 추출하여 반환

        Returns:
            [{"row_idx": 0, "data_id": "EPT_1029_10001_Q"}, ...]
        """
        index = await self._get_index(file_id)
        file_path = self._get_file_path(file_id)
        result: list[dict[str, Any]] = []

        async with aiofiles.open(file_path, mode="rb") as f:
            for i in range(index.total_lines):
                await f.seek(index.offsets[i])
                line = await f.readline()
                try:
                    data = json.loads(line.decode("utf-8").strip())
                    add_info = data.get("add_info")
                    pair_idx = ""
                    if isinstance(add_info, dict):
                        pair_idx = add_info.get("pairIDX", "")
                    result.append({
                        "row_idx": i,
                        "data_id": data.get("data_id", f"Row_{i}"),
                        "pair_idx": pair_idx,
                    })
                except (json.JSONDecodeError, UnicodeDecodeError):
                    result.append({
                        "row_idx": i,
                        "data_id": f"[Error] Row_{i}",
                    })

        return result

    async def get_row(self, file_id: str, row_idx: int) -> ItemBase:
        """인덱스 기반으로 특정 Row만 읽기 (Random Access)"""
        file_path = self._get_file_path(file_id)
        index = await self._get_index(file_id)

        if row_idx < 0 or row_idx >= index.total_lines:
            raise HTTPException(status_code=404, detail="Row not found")

        offset = index.offsets[row_idx]
        async with aiofiles.open(file_path, mode="rb") as f:
            await f.seek(offset)
            line = await f.readline()

        try:
            data = json.loads(line.decode("utf-8").strip())
            if "version" not in data:
                data["version"] = 1
            return ItemBase(**data)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Row parse error {file_id}:{row_idx} → {e}")
            raise HTTPException(status_code=500, detail="Invalid JSON data")

    async def get_row_raw(self, file_id: str, row_idx: int) -> dict[str, Any]:
        """인덱스 기반으로 특정 Row를 raw dict로 읽기"""
        file_path = self._get_file_path(file_id)
        index = await self._get_index(file_id)

        if row_idx < 0 or row_idx >= index.total_lines:
            raise HTTPException(status_code=404, detail="Row not found")

        offset = index.offsets[row_idx]
        async with aiofiles.open(file_path, mode="rb") as f:
            await f.seek(offset)
            line = await f.readline()

        try:
            return json.loads(line.decode("utf-8").strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"error": f"Parse error at row {row_idx}"}

    async def get_rows_paginated(
        self,
        file_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """페이지네이션으로 여러 Row 읽기

        Returns:
            (items, total_rows)
        """
        index = await self._get_index(file_id)
        total = index.total_lines

        start = (page - 1) * per_page
        end = min(start + per_page, total)

        if start >= total:
            return [], total

        file_path = self._get_file_path(file_id)
        items: list[dict[str, Any]] = []

        async with aiofiles.open(file_path, mode="rb") as f:
            for i in range(start, end):
                await f.seek(index.offsets[i])
                line = await f.readline()
                try:
                    items.append(json.loads(line.decode("utf-8").strip()))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    items.append({"error": f"Parse error at row {i}"})

        return items, total

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------
    async def create_backup(self, file_id: str) -> Path | None:
        """저장 전 타임스탬프 백업 생성"""
        source = self._get_file_path(file_id)
        if not source.exists():
            return None

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = self.backup_dir / f"{file_id}_{timestamp}.jsonl.bak"

        await aiofiles.os.makedirs(self.backup_dir, exist_ok=True)
        shutil.copy2(source, target)
        logger.info(f"Backup created: {target}")
        return target

    async def cleanup_old_backups(
        self, file_id: str, keep_hours: int = 24
    ) -> int:
        """오래된 백업 파일 정리 (기본 24시간 초과)"""
        cutoff = datetime.now(tz=timezone.utc).timestamp() - (keep_hours * 3600)
        removed = 0
        for bak in self.backup_dir.glob(f"{file_id}_*.jsonl.bak"):
            if bak.stat().st_mtime < cutoff:
                bak.unlink()
                removed += 1
        if removed:
            logger.info(f"Cleaned up {removed} old backups for {file_id}")
        return removed

    # ------------------------------------------------------------------
    # Atomic Write (Optimistic Locking)
    # ------------------------------------------------------------------
    async def update_row_atomic(
        self,
        file_id: str,
        row_idx: int,
        changes: dict[str, Any],
        version: int,
        user_id: str,
    ) -> dict[str, Any]:
        """Optimistic Locking + Auto Backup + Atomic Write로 Row 업데이트

        changes dict의 키-값으로 원본 Row를 부분 업데이트한다.
        편집 불가 필드(data_id 등)는 무시된다.
        원본 필드의 타입과 변경값의 타입이 다르면 거부한다.

        1. Read current raw data & 버전 체크
        2. 백업 생성
        3. 타입 검증
        4. 원본 데이터에 changes 병합
        5. 임시 파일에 쓰기
        6. os.replace로 원자적 교체
        7. 인덱스 무효화
        """
        file_path = self._get_file_path(file_id)

        editable_fields = {"content", "content_meta", "add_info"}

        # 1. Optimistic Locking
        original_data = await self.get_row_raw(file_id, row_idx)
        current_version = original_data.get("version", 1)
        if current_version != version:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Conflict: version mismatch. "
                    f"Server={current_version}, Client={version}"
                ),
            )

        # 2. Auto Backup
        await self.create_backup(file_id)

        # 3. 타입 검증 — 원본 필드의 타입과 변경값의 타입 일치 확인
        type_errors: list[str] = []
        for key, value in changes.items():
            if key not in editable_fields:
                continue
            original_value = original_data.get(key)
            if original_value is not None and value is not None:
                orig_is_dict = isinstance(original_value, dict)
                orig_is_list = isinstance(original_value, list)
                new_is_dict = isinstance(value, dict)
                new_is_list = isinstance(value, list)
                orig_is_str = isinstance(original_value, str)
                new_is_str = isinstance(value, str)

                if orig_is_dict and not new_is_dict:
                    type_errors.append(
                        f"{key}: dict → {type(value).__name__}"
                    )
                elif orig_is_list and not new_is_list:
                    type_errors.append(
                        f"{key}: list → {type(value).__name__}"
                    )
                elif orig_is_str and (new_is_dict or new_is_list):
                    type_errors.append(
                        f"{key}: str → {type(value).__name__}"
                    )

        if type_errors:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"데이터 타입 불일치: {', '.join(type_errors)}. "
                    f"원본 데이터의 구조를 유지해주세요."
                ),
            )

        # 4. 원본 데이터에 changes 병합 (허용 필드만)
        for key, value in changes.items():
            if key in editable_fields:
                original_data[key] = value

        original_data["version"] = current_version + 1
        original_data["modified_at"] = datetime.now(tz=timezone.utc).isoformat()
        original_data["modified_by"] = user_id

        new_line = json.dumps(original_data, ensure_ascii=False)

        # 5. Atomic Write
        temp_path = file_path.with_suffix(".tmp")
        try:
            async with (
                aiofiles.open(file_path, "r", encoding="utf-8") as r,
                aiofiles.open(temp_path, "w", encoding="utf-8") as w,
            ):
                lines = await r.readlines()
                if row_idx < len(lines):
                    lines[row_idx] = new_line + "\n"
                await w.writelines(lines)

            os.replace(temp_path, file_path)
            logger.info(
                f"Row updated: {file_id}:{row_idx} v{original_data['version']} by {user_id}"
            )

            # 6. 인덱스 무효화
            self._invalidate_index(file_id)

            return original_data

        except Exception as e:
            if temp_path.exists():
                os.remove(temp_path)
            logger.error(f"Atomic write failed for {file_id}:{row_idx} → {e}")
            raise HTTPException(status_code=500, detail="Save failed")


# 싱글톤 인스턴스
file_service = FileService()
