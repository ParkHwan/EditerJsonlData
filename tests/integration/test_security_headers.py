"""보안 헤더 통합 테스트 (Phase 5)

- Content-Security-Policy 존재 확인
- X-Content-Type-Options 확인
- X-Frame-Options 확인
- Referrer-Policy 확인
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import login_and_get_cookies


class TestSecurityHeaders:
    """모든 응답에 보안 헤더 포함 확인"""

    @pytest.mark.asyncio
    async def test_csp_on_html_page(self, app_client: AsyncClient) -> None:
        """HTML 뷰 응답에 CSP 헤더 포함"""
        cookies = await login_and_get_cookies(app_client)
        resp = await app_client.get("/api/v1/view/files", cookies=cookies)
        assert resp.status_code == 200

        csp = resp.headers.get("content-security-policy", "")
        assert "default-src" in csp
        assert "'self'" in csp
        assert "cdn.jsdelivr.net" in csp

    @pytest.mark.asyncio
    async def test_csp_on_api_endpoint(self, app_client: AsyncClient) -> None:
        """API JSON 응답에도 CSP 헤더 포함"""
        resp = await app_client.get("/api/v1/health")
        assert resp.status_code == 200

        csp = resp.headers.get("content-security-policy", "")
        assert "default-src" in csp

    @pytest.mark.asyncio
    async def test_x_content_type_options(self, app_client: AsyncClient) -> None:
        resp = await app_client.get("/api/v1/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    @pytest.mark.asyncio
    async def test_x_frame_options(self, app_client: AsyncClient) -> None:
        resp = await app_client.get("/api/v1/health")
        assert resp.headers.get("x-frame-options") == "DENY"

    @pytest.mark.asyncio
    async def test_referrer_policy(self, app_client: AsyncClient) -> None:
        resp = await app_client.get("/api/v1/health")
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    @pytest.mark.asyncio
    async def test_permissions_policy(self, app_client: AsyncClient) -> None:
        resp = await app_client.get("/api/v1/health")
        assert "camera=()" in resp.headers.get("permissions-policy", "")

    @pytest.mark.asyncio
    async def test_csp_frame_ancestors_none(self, app_client: AsyncClient) -> None:
        """Clickjacking 방지: frame-ancestors 'none'"""
        resp = await app_client.get("/api/v1/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp

    @pytest.mark.asyncio
    async def test_csp_websocket_connect(self, app_client: AsyncClient) -> None:
        """WebSocket 연결 허용: connect-src에 ws: 포함"""
        resp = await app_client.get("/api/v1/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "ws:" in csp
