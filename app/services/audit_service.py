"""감사 로그 서비스 (Phase 3)

사용자의 주요 행위를 JSONL 파일로 기록한다.
- 일자별 파일 분리: audit_YYYYMMDD.jsonl
- 비동기 파일 I/O (aiofiles)
- Atomic Append (O_APPEND 모드 활용)
- 오래된 로그 자동 정리 (retention_days 기준)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiofiles
import aiofiles.os
from fastapi import Request

from app.core.config import settings
from app.core.logger import logger

ActionType = Literal[
    "login",
    "logout",
    "view",
    "edit_start",
    "edit_save",
    "edit_cancel",
    "download",
    "rollback",
    "draft_save",
    "draft_restore",
    "draft_delete",
    "gcs_download",
    "gcs_upload",
]


class AuditService:
    """감사 로그 서비스 (JSONL 파일 기반)"""

    def __init__(self) -> None:
        self.audit_dir = Path(settings.AUDIT_DIR)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def _get_today_file(self) -> Path:
        """오늘 날짜의 감사 로그 파일 경로"""
        today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        return self.audit_dir / f"audit_{today}.jsonl"

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """프록시 헤더를 고려한 클라이언트 IP 추출"""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    async def log(
        self,
        *,
        action: ActionType,
        request: Request,
        user_id: str = "",
        display_name: str = "",
        file_id: str | None = None,
        row_idx: int | None = None,
        changes: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """감사 로그 기록

        Args:
            action: 행위 유형 (login, edit_save 등)
            request: FastAPI Request (IP, User-Agent 추출용)
            user_id: 사용자 ID
            display_name: 사용자 표시 이름
            file_id: 대상 파일 ID
            row_idx: 대상 Row 인덱스
            changes: 변경 내용 (diff 등)
            metadata: 추가 메타데이터
        """
        entry: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "user_id": user_id,
            "display_name": display_name,
            "action": action,
            "file_id": file_id,
            "row_idx": row_idx,
            "ip_address": self._get_client_ip(request),
            "user_agent": request.headers.get("user-agent", ""),
            "changes": changes,
            "metadata": metadata,
        }

        try:
            log_file = self._get_today_file()
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            async with aiofiles.open(log_file, mode="a", encoding="utf-8") as f:
                await f.write(line)
        except Exception as e:
            # 감사 로그 실패가 서비스를 중단시키면 안 됨
            logger.error(f"Audit log write failed: {e}")

    async def get_logs(
        self,
        date: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """감사 로그 조회

        Args:
            date: 조회할 날짜 (YYYYMMDD). None이면 오늘
            user_id: 필터링할 사용자 ID
            action: 필터링할 행위 유형
            limit: 최대 반환 건수

        Returns:
            감사 로그 목록 (최신순)
        """
        if date:
            log_file = self.audit_dir / f"audit_{date}.jsonl"
        else:
            log_file = self._get_today_file()

        if not log_file.exists():
            return []

        results: list[dict[str, Any]] = []
        try:
            async with aiofiles.open(log_file, mode="r", encoding="utf-8") as f:
                async for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue

                    # 필터링
                    if user_id and entry.get("user_id") != user_id:
                        continue
                    if action and entry.get("action") != action:
                        continue

                    results.append(entry)
        except Exception as e:
            logger.error(f"Audit log read failed: {e}")

        # 최신순 정렬 + limit 적용
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return results[:limit]

    async def cleanup_old_logs(self) -> int:
        """오래된 감사 로그 파일 정리

        Returns:
            삭제된 파일 수
        """
        cutoff = datetime.now(tz=timezone.utc).timestamp() - (
            settings.AUDIT_RETENTION_DAYS * 86400
        )
        removed = 0
        for log_file in self.audit_dir.glob("audit_*.jsonl"):
            try:
                if log_file.stat().st_mtime < cutoff:
                    await aiofiles.os.remove(str(log_file))
                    removed += 1
            except Exception as e:
                logger.error(f"Failed to remove old audit log {log_file}: {e}")

        if removed:
            logger.info(f"Cleaned up {removed} old audit log files")
        return removed


# 싱글톤 인스턴스
audit_service = AuditService()
