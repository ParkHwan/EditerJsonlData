"""Draft / Auto-save 서비스 (Phase 3)

편집 중인 데이터를 Redis에 임시 저장하여,
브라우저 종료나 네트워크 장애 시에도 작업 내용을 복구할 수 있다.

- Redis Key: draft:{file_id}:{row_idx}:{user_id}
- TTL: 30분 (설정 가능)
- 프론트엔드에서 30초 간격 자동 저장
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings
from app.core.logger import logger


class DraftService:
    """Redis 기반 Draft(임시 저장) 서비스"""

    DRAFT_PREFIX = "draft:"

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.ttl = settings.DRAFT_TTL_SECONDS

    def _get_key(self, file_id: str, row_idx: int, user_id: str) -> str:
        """Draft Redis Key 생성"""
        return f"{self.DRAFT_PREFIX}{file_id}:{row_idx}:{user_id}"

    async def save_draft(
        self,
        file_id: str,
        row_idx: int,
        user_id: str,
        content: dict[str, Any],
        version: int,
    ) -> dict[str, Any]:
        """Draft 저장 (Redis에 TTL 적용)

        Args:
            file_id: 파일 ID
            row_idx: Row 인덱스
            user_id: 사용자 ID
            content: 편집 중인 내용
            version: 원본 버전 (Optimistic Locking 용)

        Returns:
            저장된 Draft 메타데이터
        """
        key = self._get_key(file_id, row_idx, user_id)
        saved_at = datetime.now(tz=timezone.utc).isoformat()

        draft_data: dict[str, Any] = {
            "file_id": file_id,
            "row_idx": row_idx,
            "user_id": user_id,
            "content": content,
            "version": version,
            "saved_at": saved_at,
        }

        await self.redis.setex(
            key,
            self.ttl,
            json.dumps(draft_data, ensure_ascii=False),
        )
        logger.debug(f"Draft saved: {key}")

        return {
            "file_id": file_id,
            "row_idx": row_idx,
            "saved_at": saved_at,
            "ttl": self.ttl,
        }

    async def get_draft(
        self,
        file_id: str,
        row_idx: int,
        user_id: str,
    ) -> dict[str, Any] | None:
        """Draft 조회

        Returns:
            Draft 데이터 dict 또는 None (없거나 만료됨)
        """
        key = self._get_key(file_id, row_idx, user_id)
        raw = await self.redis.get(key)
        if not raw:
            return None

        try:
            data: dict[str, Any] = json.loads(raw)
            # 남은 TTL 추가
            remaining_ttl = await self.redis.ttl(key)
            data["remaining_ttl"] = max(0, remaining_ttl)
            return data
        except (json.JSONDecodeError, TypeError):
            return None

    async def delete_draft(
        self,
        file_id: str,
        row_idx: int,
        user_id: str,
    ) -> bool:
        """Draft 삭제

        Returns:
            삭제 여부
        """
        key = self._get_key(file_id, row_idx, user_id)
        deleted = await self.redis.delete(key)
        if deleted:
            logger.debug(f"Draft deleted: {key}")
        return bool(deleted)

    async def list_user_drafts(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """특정 사용자의 모든 Draft 목록 조회

        Returns:
            Draft 메타데이터 리스트
        """
        pattern = f"{self.DRAFT_PREFIX}*:{user_id}"
        drafts: list[dict[str, Any]] = []

        async for key in self.redis.scan_iter(match=pattern, count=100):
            raw = await self.redis.get(key)
            if not raw:
                continue
            try:
                data: dict[str, Any] = json.loads(raw)
                remaining_ttl = await self.redis.ttl(key)
                drafts.append(
                    {
                        "file_id": data.get("file_id", ""),
                        "row_idx": data.get("row_idx", 0),
                        "saved_at": data.get("saved_at", ""),
                        "remaining_ttl": max(0, remaining_ttl),
                    }
                )
            except (json.JSONDecodeError, TypeError):
                continue

        # 최신순 정렬
        drafts.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
        return drafts

    async def has_draft(
        self,
        file_id: str,
        row_idx: int,
        user_id: str,
    ) -> bool:
        """Draft 존재 여부 확인"""
        key = self._get_key(file_id, row_idx, user_id)
        return bool(await self.redis.exists(key))
