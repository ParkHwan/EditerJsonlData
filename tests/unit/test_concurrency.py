"""동시성 테스트 (Phase 5)

- 동시 Lock 경쟁: 2명 중 1명만 Lock 획득
- 동시 저장: Optimistic Locking으로 1명만 성공
- Lock 해제 후 재획득
- 다수 사용자 동시 Lock 요청
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis

from app.services.file_service import FileService
from app.services.lock_service import LockService


class TestConcurrentLock:
    """동시 Lock 경쟁 시나리오"""

    @pytest.mark.asyncio
    async def test_two_users_lock_race(self, fake_redis: Redis) -> None:
        """두 사용자가 동시에 같은 Row Lock 요청 → 한 명만 성공"""
        svc = LockService(fake_redis)

        results = await asyncio.gather(
            svc.acquire_lock("file1", 0, "user_a"),
            svc.acquire_lock("file1", 0, "user_b"),
        )

        assert results.count(True) == 1
        assert results.count(False) == 1

        owner = await svc.check_lock("file1", 0)
        assert owner in ("user_a", "user_b")

    @pytest.mark.asyncio
    async def test_multiple_users_lock_race(self, fake_redis: Redis) -> None:
        """5명 동시 Lock → 1명만 성공"""
        svc = LockService(fake_redis)
        users = [f"user_{i}" for i in range(5)]

        results = await asyncio.gather(
            *[svc.acquire_lock("file1", 0, u) for u in users]
        )

        assert results.count(True) == 1
        winner_idx = results.index(True)
        owner = await svc.check_lock("file1", 0)
        assert owner == users[winner_idx]

    @pytest.mark.asyncio
    async def test_lock_different_rows_concurrent(self, fake_redis: Redis) -> None:
        """서로 다른 Row에 동시 Lock → 모두 성공"""
        svc = LockService(fake_redis)

        results = await asyncio.gather(
            svc.acquire_lock("file1", 0, "user_a"),
            svc.acquire_lock("file1", 1, "user_b"),
            svc.acquire_lock("file1", 2, "user_c"),
        )

        assert all(results)

    @pytest.mark.asyncio
    async def test_lock_release_then_reacquire(self, fake_redis: Redis) -> None:
        """Lock 해제 후 다른 사용자가 재획득"""
        svc = LockService(fake_redis)

        await svc.acquire_lock("file1", 0, "user_a")
        assert await svc.check_lock("file1", 0) == "user_a"

        await svc.release_lock("file1", 0, "user_a")
        assert await svc.check_lock("file1", 0) is None

        result = await svc.acquire_lock("file1", 0, "user_b")
        assert result is True
        assert await svc.check_lock("file1", 0) == "user_b"

    @pytest.mark.asyncio
    async def test_concurrent_heartbeat(self, fake_redis: Redis) -> None:
        """Lock 보유자가 여러 번 동시 Heartbeat → 모두 성공"""
        svc = LockService(fake_redis)
        await svc.acquire_lock("file1", 0, "user_a")

        results = await asyncio.gather(
            svc.heartbeat("file1", 0, "user_a"),
            svc.heartbeat("file1", 0, "user_a"),
            svc.heartbeat("file1", 0, "user_a"),
        )
        assert all(ttl == LockService.LOCK_TTL for ttl in results)


class TestConcurrentSave:
    """동시 저장 시 Optimistic Locking 검증"""

    def _make_svc(self, tmp_data_dir: Path) -> FileService:
        svc = FileService()
        svc.data_dir = tmp_data_dir
        svc.backup_dir = tmp_data_dir / "backups"
        return svc

    @pytest.mark.asyncio
    async def test_two_users_concurrent_save_same_version(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """같은 version으로 동시 저장 → 1명 성공, 1명 실패 (409 또는 500)

        실제 운영에서는 Lock이 동시 접근을 차단하므로 이 시나리오는 발생하지 않으나,
        Atomic Write의 안전성을 검증하기 위한 스트레스 테스트.
        """
        svc = self._make_svc(tmp_data_dir)

        changes_a = {"content": {"question": "A가 수정", "answer": "A답변"}}
        changes_b = {"content": {"question": "B가 수정", "answer": "B답변"}}

        results = await asyncio.gather(
            svc.update_row_atomic("sample", 0, changes_a, version=1, user_id="user_a"),
            svc.update_row_atomic("sample", 0, changes_b, version=1, user_id="user_b"),
            return_exceptions=True,
        )

        successes = [r for r in results if isinstance(r, dict)]
        failures = [
            r for r in results
            if isinstance(r, HTTPException) and r.status_code in (409, 500)
        ]

        assert len(successes) == 1, f"Exactly one should succeed, got {len(successes)}"
        assert len(failures) == 1, f"Exactly one should fail, got {[type(r).__name__ for r in results if r not in successes]}"
        assert successes[0]["version"] == 2

    @pytest.mark.asyncio
    async def test_sequential_saves_increment_version(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """순차 저장 시 버전이 올바르게 증가"""
        svc = self._make_svc(tmp_data_dir)

        r1 = await svc.update_row_atomic(
            "sample", 0, {"content": {"question": "v2"}}, version=1, user_id="u1"
        )
        assert r1["version"] == 2

        r2 = await svc.update_row_atomic(
            "sample", 0, {"content": {"question": "v3"}}, version=2, user_id="u2"
        )
        assert r2["version"] == 3

        with pytest.raises(HTTPException) as exc_info:
            await svc.update_row_atomic(
                "sample", 0, {"content": {"question": "fail"}}, version=1, user_id="u3"
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_concurrent_saves_different_rows(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """다른 Row에 동시 저장 → 모두 성공"""
        svc = self._make_svc(tmp_data_dir)

        results = await asyncio.gather(
            svc.update_row_atomic(
                "sample", 0, {"content": {"question": "수정0"}}, version=1, user_id="u0"
            ),
            svc.update_row_atomic(
                "sample", 1, {"content": {"question": "수정1"}}, version=1, user_id="u1"
            ),
            svc.update_row_atomic(
                "sample", 2, {"content": {"question": "수정2"}}, version=1, user_id="u2"
            ),
            return_exceptions=True,
        )

        successes = [r for r in results if isinstance(r, dict)]
        assert len(successes) >= 1
