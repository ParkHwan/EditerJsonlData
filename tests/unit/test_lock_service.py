"""LockService 단위 테스트

- Lock 획득 / 해제
- Heartbeat (TTL 연장)
- 다른 사용자 Lock 충돌
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis

from app.services.lock_service import LockService


class TestLockService:
    """Redis 분산 Lock 서비스 테스트"""

    @pytest.mark.asyncio
    async def test_acquire_lock(self, fake_redis: Redis) -> None:
        """Lock 획득 성공"""
        svc = LockService(fake_redis)
        result = await svc.acquire_lock("file1", 0, "user_a")
        assert result is True

        owner = await svc.check_lock("file1", 0)
        assert owner == "user_a"

    @pytest.mark.asyncio
    async def test_acquire_lock_same_user_refresh(self, fake_redis: Redis) -> None:
        """동일 사용자 재획득 시 TTL 갱신"""
        svc = LockService(fake_redis)
        await svc.acquire_lock("file1", 0, "user_a")
        result = await svc.acquire_lock("file1", 0, "user_a")
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_lock_conflict(self, fake_redis: Redis) -> None:
        """다른 사용자가 이미 Lock 보유 시 실패"""
        svc = LockService(fake_redis)
        await svc.acquire_lock("file1", 0, "user_a")
        result = await svc.acquire_lock("file1", 0, "user_b")
        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock(self, fake_redis: Redis) -> None:
        """Lock 해제"""
        svc = LockService(fake_redis)
        await svc.acquire_lock("file1", 0, "user_a")
        await svc.release_lock("file1", 0, "user_a")

        owner = await svc.check_lock("file1", 0)
        assert owner is None

    @pytest.mark.asyncio
    async def test_release_lock_by_non_owner(self, fake_redis: Redis) -> None:
        """소유자가 아닌 사용자의 Lock 해제 → 무시"""
        svc = LockService(fake_redis)
        await svc.acquire_lock("file1", 0, "user_a")
        await svc.release_lock("file1", 0, "user_b")

        owner = await svc.check_lock("file1", 0)
        assert owner == "user_a"  # 여전히 user_a 소유

    @pytest.mark.asyncio
    async def test_heartbeat(self, fake_redis: Redis) -> None:
        """Heartbeat TTL 연장"""
        svc = LockService(fake_redis)
        await svc.acquire_lock("file1", 0, "user_a")
        ttl = await svc.heartbeat("file1", 0, "user_a")
        assert ttl == LockService.LOCK_TTL

    @pytest.mark.asyncio
    async def test_heartbeat_expired_lock(self, fake_redis: Redis) -> None:
        """만료된 Lock에 Heartbeat → 404"""
        svc = LockService(fake_redis)
        with pytest.raises(HTTPException) as exc_info:
            await svc.heartbeat("file1", 0, "user_a")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_heartbeat_wrong_owner(self, fake_redis: Redis) -> None:
        """다른 사용자의 Lock에 Heartbeat → 403"""
        svc = LockService(fake_redis)
        await svc.acquire_lock("file1", 0, "user_a")
        with pytest.raises(HTTPException) as exc_info:
            await svc.heartbeat("file1", 0, "user_b")
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_check_lock_no_lock(self, fake_redis: Redis) -> None:
        """Lock 없는 상태 → None"""
        svc = LockService(fake_redis)
        owner = await svc.check_lock("file1", 0)
        assert owner is None
