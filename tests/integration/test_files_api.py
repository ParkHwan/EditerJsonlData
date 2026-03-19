"""Files API 통합 테스트

- GET /api/v1/view/login
- GET /api/v1/view/files
- GET /api/v1/view/files/{file_id}
- GET /api/v1/view/files/{file_id}/download
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import login_and_get_cookies


class TestFilesAPI:
    """파일 뷰 API 통합 테스트"""

    @pytest.mark.asyncio
    async def test_login_page(self, app_client: AsyncClient) -> None:
        """로그인 페이지 렌더링"""
        resp = await app_client.get("/api/v1/view/login")
        assert resp.status_code == 200
        assert "로그인" in resp.text

    @pytest.mark.asyncio
    async def test_file_list_unauthenticated(self, app_client: AsyncClient) -> None:
        """미인증 → 로그인 페이지 리다이렉트"""
        resp = await app_client.get("/api/v1/view/files", follow_redirects=False)
        assert resp.status_code == 307
        assert "/view/login" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_file_list_authenticated(self, app_client: AsyncClient) -> None:
        """인증 후 파일 목록 렌더링"""
        cookies = await login_and_get_cookies(app_client)
        resp = await app_client.get("/api/v1/view/files", cookies=cookies)

        assert resp.status_code == 200
        assert "sample.jsonl" in resp.text

    @pytest.mark.asyncio
    async def test_view_file(self, app_client: AsyncClient) -> None:
        """파일 뷰어 렌더링"""
        cookies = await login_and_get_cookies(app_client)
        resp = await app_client.get("/api/v1/view/files/sample", cookies=cookies)

        assert resp.status_code == 200
        assert "sample.jsonl" in resp.text
        assert "문제 1" in resp.text or "총" in resp.text

    @pytest.mark.asyncio
    async def test_view_file_not_found(self, app_client: AsyncClient) -> None:
        """존재하지 않는 파일 → 404"""
        cookies = await login_and_get_cookies(app_client)
        resp = await app_client.get("/api/v1/view/files/nonexistent", cookies=cookies)

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_view_file_has_csrf_token(self, app_client: AsyncClient) -> None:
        """뷰 페이지에 CSRF 토큰 존재"""
        cookies = await login_and_get_cookies(app_client)
        resp = await app_client.get("/api/v1/view/files/sample", cookies=cookies)

        assert resp.status_code == 200
        assert 'name="csrf-token"' in resp.text
        assert "csrf_token" in resp.cookies

    @pytest.mark.asyncio
    async def test_download_file(self, app_client: AsyncClient) -> None:
        """파일 다운로드"""
        cookies = await login_and_get_cookies(app_client)
        resp = await app_client.get(
            "/api/v1/view/files/sample/download", cookies=cookies
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/json")

    @pytest.mark.asyncio
    async def test_download_unauthenticated(self, app_client: AsyncClient) -> None:
        """미인증 다운로드 → 401"""
        resp = await app_client.get("/api/v1/view/files/sample/download")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_root_redirect(self, app_client: AsyncClient) -> None:
        """루트 경로 → 파일 목록 리다이렉트"""
        resp = await app_client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert "/view/files" in resp.headers.get("location", "")
