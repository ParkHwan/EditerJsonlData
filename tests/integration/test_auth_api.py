"""Auth API 통합 테스트

- POST /api/v1/auth/login
- POST /api/v1/auth/logout
- GET  /api/v1/auth/me
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import login_and_get_cookies


class TestAuthAPI:
    """인증 API 통합 테스트"""

    @pytest.mark.asyncio
    async def test_login_success(self, app_client: AsyncClient) -> None:
        """로그인 성공"""
        resp = await app_client.post(
            "/api/v1/auth/login",
            json={"user_id": "hong", "display_name": "홍길동"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["user_id"] == "hong"
        assert "session_id" in resp.cookies

    @pytest.mark.asyncio
    async def test_login_validation_error(self, app_client: AsyncClient) -> None:
        """로그인 유효성 검사 실패 (빈 user_id)"""
        resp = await app_client.post(
            "/api/v1/auth/login",
            json={"user_id": "", "display_name": "테스트"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_me(self, app_client: AsyncClient) -> None:
        """현재 사용자 정보 조회"""
        cookies = await login_and_get_cookies(app_client, "me_user", "나")
        resp = await app_client.get("/api/v1/auth/me", cookies=cookies)

        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "me_user"
        assert data["display_name"] == "나"

    @pytest.mark.asyncio
    async def test_get_me_unauthorized(self, app_client: AsyncClient) -> None:
        """미인증 상태에서 /me → 401"""
        resp = await app_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_logout(self, app_client: AsyncClient) -> None:
        """로그아웃 후 세션 무효화"""
        cookies = await login_and_get_cookies(app_client)

        # 로그아웃
        resp = await app_client.post("/api/v1/auth/logout", cookies=cookies)
        assert resp.status_code == 200

        # 로그아웃 후 /me → 401
        resp2 = await app_client.get("/api/v1/auth/me", cookies=cookies)
        assert resp2.status_code == 401
