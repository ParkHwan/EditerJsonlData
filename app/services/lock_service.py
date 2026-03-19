from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from redis.asyncio import Redis

from app.core.logger import logger


class LockService:
    LOCK_TTL = 3600  # 60 minutes
    
    def __init__(self, redis: Redis):
        self.redis = redis

    def _get_key(self, file_id: str, row_idx: int) -> str:
        return f"lock:{file_id}:{row_idx}"

    async def acquire_lock(self, file_id: str, row_idx: int, user_id: str) -> bool:
        """
        Acquire a lock for a specific row in a file.
        Returns True if acquired, False if already locked by another user.
        If already locked by the SAME user, it refreshes the TTL.
        """
        key = self._get_key(file_id, row_idx)
        current_owner = await self.redis.get(key)

        if current_owner and current_owner != user_id:
            return False
        
        # Acquire or Refresh
        await self.redis.set(key, user_id, ex=self.LOCK_TTL)
        logger.info(f"Lock acquired: {key} by {user_id}")
        return True

    async def heartbeat(self, file_id: str, row_idx: int, user_id: str) -> int:
        """
        Extends the lock TTL if the user owns it.
        Returns remaining TTL in seconds.
        """
        key = self._get_key(file_id, row_idx)
        current_owner = await self.redis.get(key)

        if not current_owner:
            raise HTTPException(status_code=404, detail="Lock expired or not found")
        
        if current_owner != user_id:
            raise HTTPException(status_code=403, detail="Lock owned by another user")

        # Extend TTL
        await self.redis.expire(key, self.LOCK_TTL)
        return self.LOCK_TTL

    async def release_lock(self, file_id: str, row_idx: int, user_id: str):
        """Release the lock if owned by user."""
        key = self._get_key(file_id, row_idx)
        current_owner = await self.redis.get(key)

        if current_owner == user_id:
            await self.redis.delete(key)
            logger.info(f"Lock released: {key} by {user_id}")
        # If not owner or no lock, do nothing (idempotent)

    async def check_lock(self, file_id: str, row_idx: int) -> str | None:
        """Returns the user_id holding the lock, or None."""
        key = self._get_key(file_id, row_idx)
        return await self.redis.get(key)

    async def get_all_locks(self, file_id: str) -> list[dict[str, Any]]:
        """file_id에 해당하는 모든 Lock 정보 반환 (WebSocket 초기 상태용)."""
        pattern = f"lock:{file_id}:*"
        locks: list[dict[str, Any]] = []
        async for key in self.redis.scan_iter(match=pattern):
            owner = await self.redis.get(key)
            if owner is None:
                continue
            parts = key.split(":")
            if len(parts) >= 3:
                try:
                    row_idx = int(parts[-1])
                    locks.append({"row_idx": row_idx, "user_id": owner})
                except ValueError:
                    continue
        return locks
