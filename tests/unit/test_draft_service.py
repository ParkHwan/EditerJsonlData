"""DraftService 단위 테스트

- Draft CRUD (저장 / 조회 / 삭제)
- TTL 확인
- 사용자별 Draft 목록
"""

from __future__ import annotations

import pytest
from redis.asyncio import Redis

from app.services.draft_service import DraftService


class TestDraftService:
    """Redis 기반 Draft 서비스 테스트"""

    @pytest.mark.asyncio
    async def test_save_and_get_draft(self, fake_redis: Redis) -> None:
        """Draft 저장 및 조회"""
        svc = DraftService(fake_redis)

        await svc.save_draft(
            file_id="sample",
            row_idx=0,
            user_id="user1",
            content={"question": "임시 저장"},
            version=1,
        )

        draft = await svc.get_draft("sample", 0, "user1")
        assert draft is not None
        assert draft["content"]["question"] == "임시 저장"
        assert draft["version"] == 1
        assert draft["remaining_ttl"] > 0

    @pytest.mark.asyncio
    async def test_get_nonexistent_draft(self, fake_redis: Redis) -> None:
        """존재하지 않는 Draft → None"""
        svc = DraftService(fake_redis)
        draft = await svc.get_draft("sample", 99, "user1")
        assert draft is None

    @pytest.mark.asyncio
    async def test_delete_draft(self, fake_redis: Redis) -> None:
        """Draft 삭제"""
        svc = DraftService(fake_redis)

        await svc.save_draft("sample", 0, "user1", {"q": "temp"}, 1)
        deleted = await svc.delete_draft("sample", 0, "user1")
        assert deleted is True

        draft = await svc.get_draft("sample", 0, "user1")
        assert draft is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_draft(self, fake_redis: Redis) -> None:
        """존재하지 않는 Draft 삭제 → False"""
        svc = DraftService(fake_redis)
        deleted = await svc.delete_draft("sample", 99, "user1")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_has_draft(self, fake_redis: Redis) -> None:
        """Draft 존재 여부"""
        svc = DraftService(fake_redis)

        assert await svc.has_draft("sample", 0, "user1") is False

        await svc.save_draft("sample", 0, "user1", {"q": "temp"}, 1)
        assert await svc.has_draft("sample", 0, "user1") is True

    @pytest.mark.asyncio
    async def test_overwrite_draft(self, fake_redis: Redis) -> None:
        """동일 Key Draft 덮어쓰기"""
        svc = DraftService(fake_redis)

        await svc.save_draft("sample", 0, "user1", {"q": "v1"}, 1)
        await svc.save_draft("sample", 0, "user1", {"q": "v2"}, 1)

        draft = await svc.get_draft("sample", 0, "user1")
        assert draft is not None
        assert draft["content"]["q"] == "v2"

    @pytest.mark.asyncio
    async def test_different_users_different_drafts(self, fake_redis: Redis) -> None:
        """다른 사용자의 Draft는 독립적"""
        svc = DraftService(fake_redis)

        await svc.save_draft("sample", 0, "user_a", {"q": "A의 Draft"}, 1)
        await svc.save_draft("sample", 0, "user_b", {"q": "B의 Draft"}, 1)

        draft_a = await svc.get_draft("sample", 0, "user_a")
        draft_b = await svc.get_draft("sample", 0, "user_b")

        assert draft_a is not None
        assert draft_b is not None
        assert draft_a["content"]["q"] == "A의 Draft"
        assert draft_b["content"]["q"] == "B의 Draft"

    @pytest.mark.asyncio
    async def test_list_user_drafts(self, fake_redis: Redis) -> None:
        """사용자 Draft 목록"""
        svc = DraftService(fake_redis)

        await svc.save_draft("file1", 0, "user1", {"q": "1"}, 1)
        await svc.save_draft("file1", 1, "user1", {"q": "2"}, 1)
        await svc.save_draft("file2", 0, "user1", {"q": "3"}, 1)
        await svc.save_draft("file1", 0, "user2", {"q": "other"}, 1)  # 다른 사용자

        drafts = await svc.list_user_drafts("user1")
        assert len(drafts) == 3

        # 다른 사용자의 Draft는 포함되지 않음
        drafts_2 = await svc.list_user_drafts("user2")
        assert len(drafts_2) == 1
