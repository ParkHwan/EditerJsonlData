"""AuthService 단위 테스트

- 세션 생성 / 삭제 / 검증
- 세션 고정 공격 방지 (old session 무효화)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis

from app.services.auth_service import AuthService


def _make_request(ip: str = "127.0.0.1", ua: str = "TestBot/1.0") -> MagicMock:
    """Mock Request 객체 생성"""
    request = MagicMock()
    request.headers = {"user-agent": ua}
    request.client = MagicMock()
    request.client.host = ip
    return request


class TestAuthService:
    """Redis 세션 기반 인증 서비스 테스트"""

    @pytest.mark.asyncio
    async def test_create_session(self, fake_redis: Redis) -> None:
        """세션 생성 성공"""
        svc = AuthService(fake_redis)
        request = _make_request()

        session_id = await svc.create_session(
            user_id="test_user",
            display_name="테스트",
            request=request,
        )

        assert session_id is not None
        assert len(session_id) > 10

    @pytest.mark.asyncio
    async def test_validate_session(self, fake_redis: Redis) -> None:
        """세션 검증 성공"""
        svc = AuthService(fake_redis)
        request = _make_request()

        session_id = await svc.create_session("user1", "유저1", request)
        data = await svc.validate_session(session_id, request)

        assert data is not None
        assert data["user_id"] == "user1"
        assert data["display_name"] == "유저1"

    @pytest.mark.asyncio
    async def test_validate_invalid_session(self, fake_redis: Redis) -> None:
        """존재하지 않는 세션 → None"""
        svc = AuthService(fake_redis)
        request = _make_request()

        data = await svc.validate_session("nonexistent", request)
        assert data is None

    @pytest.mark.asyncio
    async def test_destroy_session(self, fake_redis: Redis) -> None:
        """세션 삭제"""
        svc = AuthService(fake_redis)
        request = _make_request()

        session_id = await svc.create_session("user1", "유저1", request)
        await svc.destroy_session(session_id)

        data = await svc.validate_session(session_id, request)
        assert data is None

    @pytest.mark.asyncio
    async def test_session_fixation_prevention(self, fake_redis: Redis) -> None:
        """세션 고정 공격 방지: 재로그인 시 이전 세션 무효화"""
        svc = AuthService(fake_redis)
        request = _make_request()

        old_session = await svc.create_session("user1", "유저1", request)
        new_session = await svc.create_session(
            "user1", "유저1", request, old_session_id=old_session
        )

        assert old_session != new_session

        # 이전 세션은 무효
        old_data = await svc.validate_session(old_session, request)
        assert old_data is None

        # 새 세션은 유효
        new_data = await svc.validate_session(new_session, request)
        assert new_data is not None
        assert new_data["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_get_session_user_success(self, fake_redis: Redis) -> None:
        """get_session_user: 유효한 세션"""
        svc = AuthService(fake_redis)
        request = _make_request()

        session_id = await svc.create_session("user1", "유저1", request)
        user = await svc.get_session_user(session_id, request)

        assert user["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_get_session_user_invalid(self, fake_redis: Redis) -> None:
        """get_session_user: 유효하지 않은 세션 → 401"""
        svc = AuthService(fake_redis)
        request = _make_request()

        with pytest.raises(HTTPException) as exc_info:
            await svc.get_session_user("bad_session", request)
        assert exc_info.value.status_code == 401
