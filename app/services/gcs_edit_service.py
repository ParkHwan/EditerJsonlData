"""GCS 파일 Redis 기반 편집 서비스

GCS JSONL을 로컬 파일 없이 직접 편집하기 위한 서비스.
전체 흐름:
    1. load_from_gcs()  → GCS JSONL → Redis Hash (행별 저장)
    2. get_row()         → Redis에서 단일 행 읽기
    3. update_row()      → Redis에서 행 업데이트 (변경분만 병합)
    4. publish_to_gcs()  → Redis 전체 행 → JSONL 재구성 → GCS 업데이트
    5. discard()         → Redis 작업 사본 삭제 (편집 취소)

Redis 키 구조:
    gcs_wc:{file_id}:rows  → Hash { "0": json_str, "1": json_str, ... }
    gcs_wc:{file_id}:meta  → Hash { gcs_path, date_str, total_rows, loaded_at }
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.core.logger import logger
from app.db.redis_client import get_redis_client
from app.services.gcs_service import gcs_service

WORKING_COPY_PREFIX = "gcs_wc"
WORKING_COPY_TTL = 86400  # 24시간

EDITABLE_FIELDS = {"content", "content_meta", "add_info"}


class GCSEditService:
    """GCS 파일의 Redis 기반 편집 서비스"""

    def _rows_key(self, file_id: str) -> str:
        return f"{WORKING_COPY_PREFIX}:{file_id}:rows"

    def _meta_key(self, file_id: str) -> str:
        return f"{WORKING_COPY_PREFIX}:{file_id}:meta"

    async def is_loaded(self, file_id: str) -> bool:
        """파일이 Redis working copy에 로드되어 있는지 확인"""
        redis = await get_redis_client()
        return await redis.exists(self._meta_key(file_id)) > 0

    async def load_from_gcs(self, file_id: str, gcs_path: str, date_str: str) -> int:
        """GCS JSONL을 Redis working copy로 로드

        Returns:
            로드된 총 행 수
        """
        redis = await get_redis_client()
        rows_key = self._rows_key(file_id)
        meta_key = self._meta_key(file_id)

        def _download_text() -> str:
            blob = gcs_service.bucket.blob(gcs_path)
            return blob.download_as_text(encoding="utf-8")

        raw_text = await asyncio.to_thread(_download_text)
        lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]

        pipe = redis.pipeline()
        await pipe.delete(rows_key)
        for idx, line in enumerate(lines):
            row = json.loads(line)
            if "_version" not in row:
                row["_version"] = 1
            await pipe.hset(rows_key, str(idx), json.dumps(row, ensure_ascii=False))
        await pipe.execute()

        await redis.hset(meta_key, mapping={
            "gcs_path": gcs_path,
            "date_str": date_str,
            "total_rows": str(len(lines)),
            "loaded_at": datetime.now(tz=timezone.utc).isoformat(),
        })

        await redis.expire(rows_key, WORKING_COPY_TTL)
        await redis.expire(meta_key, WORKING_COPY_TTL)

        logger.info(f"GCS → Redis: {file_id} ({len(lines)} rows) from {gcs_path}")
        return len(lines)

    async def get_meta(self, file_id: str) -> dict[str, str] | None:
        """working copy 메타데이터 조회"""
        redis = await get_redis_client()
        meta = await redis.hgetall(self._meta_key(file_id))
        return meta if meta else None

    async def get_row(self, file_id: str, row_idx: int) -> dict[str, Any]:
        """Redis에서 단일 행 읽기"""
        redis = await get_redis_client()
        raw = await redis.hget(self._rows_key(file_id), str(row_idx))
        if raw is None:
            raise KeyError(f"Row {row_idx} not found in working copy: {file_id}")
        row = json.loads(raw)
        row["row_idx"] = row_idx
        row["file_id"] = file_id
        return row

    async def update_row(
        self,
        file_id: str,
        row_idx: int,
        changes: dict[str, Any],
        version: int,
        user_id: str,
    ) -> dict[str, Any]:
        """Redis에서 행 업데이트 (변경분만 병합, Optimistic Locking)"""
        redis = await get_redis_client()
        rows_key = self._rows_key(file_id)

        raw = await redis.hget(rows_key, str(row_idx))
        if raw is None:
            raise KeyError(f"Row {row_idx} not found in working copy: {file_id}")

        row = json.loads(raw)
        current_version = row.get("_version", 1)

        if current_version != version:
            raise ValueError(
                f"Version conflict: expected {version}, got {current_version}"
            )

        for key, value in changes.items():
            if key in EDITABLE_FIELDS:
                row[key] = value

        row["_version"] = current_version + 1
        row["_last_edited_by"] = user_id
        row["_last_edited_at"] = datetime.now(tz=timezone.utc).isoformat()

        await redis.hset(rows_key, str(row_idx), json.dumps(row, ensure_ascii=False))

        await redis.expire(rows_key, WORKING_COPY_TTL)
        await redis.expire(self._meta_key(file_id), WORKING_COPY_TTL)

        logger.info(f"Redis row updated: {file_id}[{row_idx}] by {user_id}")
        return row

    async def get_data_id_list(self, file_id: str) -> list[dict[str, Any]]:
        """모든 행의 data_id 목록 반환 (사이드바용)"""
        redis = await get_redis_client()
        rows_key = self._rows_key(file_id)
        all_rows = await redis.hgetall(rows_key)

        items: list[dict[str, Any]] = []
        for idx_str in sorted(all_rows.keys(), key=int):
            row = json.loads(all_rows[idx_str])
            add_info = row.get("add_info")
            pair_idx = ""
            if isinstance(add_info, dict):
                pair_idx = add_info.get("pairIDX", "")
            items.append({
                "row_idx": int(idx_str),
                "data_id": row.get("data_id", f"row_{idx_str}"),
                "pair_idx": pair_idx,
            })
        return items

    async def publish_to_gcs(self, file_id: str) -> str:
        """Redis의 모든 행을 JSONL로 재구성 → GCS 덮어쓰기

        Returns:
            업로드된 GCS 경로
        """
        redis = await get_redis_client()
        meta = await redis.hgetall(self._meta_key(file_id))
        if not meta:
            raise KeyError(f"Working copy not found: {file_id}")

        gcs_path = meta["gcs_path"]
        all_rows = await redis.hgetall(self._rows_key(file_id))

        lines: list[str] = []
        for idx_str in sorted(all_rows.keys(), key=int):
            row = json.loads(all_rows[idx_str])
            row.pop("_version", None)
            row.pop("_last_edited_by", None)
            row.pop("_last_edited_at", None)
            lines.append(json.dumps(row, ensure_ascii=False))

        jsonl_content = "\n".join(lines) + "\n"

        def _upload() -> None:
            blob = gcs_service.bucket.blob(gcs_path)
            blob.upload_from_string(jsonl_content, content_type="application/json")

        await asyncio.to_thread(_upload)
        logger.info(f"Redis → GCS updated: {file_id} ({len(lines)} rows) → {gcs_path}")

        await gcs_service.invalidate_cache()
        return gcs_path

    async def discard(self, file_id: str) -> None:
        """Redis working copy 삭제 (편집 취소)"""
        redis = await get_redis_client()
        await redis.delete(self._rows_key(file_id), self._meta_key(file_id))
        logger.info(f"Working copy discarded: {file_id}")

    async def list_active_sessions(self) -> list[dict[str, str]]:
        """Redis에 현재 로드된 모든 GCS 편집 세션의 메타 정보 반환"""
        redis = await get_redis_client()
        sessions: list[dict[str, str]] = []
        async for key in redis.scan_iter(
            match=f"{WORKING_COPY_PREFIX}:*:meta", count=100
        ):
            meta = await redis.hgetall(key)
            if meta:
                file_id = key.split(":")[1] if ":" in key else ""
                sessions.append({
                    "file_id": file_id,
                    "gcs_path": meta.get("gcs_path", ""),
                    "date_str": meta.get("date_str", ""),
                    "total_rows": meta.get("total_rows", "0"),
                    "loaded_at": meta.get("loaded_at", ""),
                })
        return sessions

    async def refresh_ttl(self, file_id: str) -> None:
        """working copy TTL 갱신 (편집 활동 시)"""
        redis = await get_redis_client()
        await redis.expire(self._rows_key(file_id), WORKING_COPY_TTL)
        await redis.expire(self._meta_key(file_id), WORKING_COPY_TTL)


gcs_edit_service = GCSEditService()
