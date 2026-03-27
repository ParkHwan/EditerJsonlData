from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from redis.asyncio import Redis

from app.core.logger import logger


class LockService:
    LOCK_TTL = 3600  # 60 minutes

    def __init__(self, redis: Redis):
        self.redis = redis

    def _get_key(self, file_id: str) -> str:
        return f"lock:{file_id}"

    async def acquire_lock(self, file_id: str, user_id: str) -> bool:
        """파일 단위 Lock 획득. 동일 사용자면 TTL 갱신."""
        key = self._get_key(file_id)
        current_owner = await self.redis.get(key)

        if current_owner and current_owner != user_id:
            return False

        await self.redis.set(key, user_id, ex=self.LOCK_TTL)
        logger.info(f"File lock acquired: {key} by {user_id}")
        return True

    async def heartbeat(self, file_id: str, user_id: str) -> int:
        """Lock TTL 연장. 소유자만 가능."""
        key = self._get_key(file_id)
        current_owner = await self.redis.get(key)

        if not current_owner:
            raise HTTPException(status_code=404, detail="Lock expired or not found")

        if current_owner != user_id:
            raise HTTPException(status_code=403, detail="Lock owned by another user")

        await self.redis.expire(key, self.LOCK_TTL)
        return self.LOCK_TTL

    async def release_lock(self, file_id: str, user_id: str) -> None:
        """Lock 해제 (소유자만)."""
        key = self._get_key(file_id)
        current_owner = await self.redis.get(key)

        if current_owner == user_id:
            await self.redis.delete(key)
            logger.info(f"File lock released: {key} by {user_id}")

    async def release_lock_force(self, file_id: str) -> None:
        """Lock 강제 해제 (stale lock 정리용). 소유자 확인 없이 삭제."""
        key = self._get_key(file_id)
        await self.redis.delete(key)
        logger.info("File lock force-released: %s", key)

    async def check_lock(self, file_id: str) -> str | None:
        """파일 Lock 소유자 반환. 없으면 None."""
        key = self._get_key(file_id)
        return await self.redis.get(key)

    async def get_lock_info(self, file_id: str) -> dict[str, Any] | None:
        """파일 Lock 상세 정보 반환 (WebSocket 초기 상태용)."""
        owner = await self.check_lock(file_id)
        if owner:
            return {"user_id": owner, "file_id": file_id}
        return None
