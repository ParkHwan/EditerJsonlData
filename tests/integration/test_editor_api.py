"""Editor API 통합 테스트

- Lock 획득 / Heartbeat / 해제
- Data 읽기 / 저장 (changes dict 방식)
- CSRF 토큰 검증
- Optimistic Locking
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

from tests.conftest import login_and_get_cookies


async def _get_csrf(client: AsyncClient, cookies: dict[str, str]) -> tuple[dict[str, str], str]:
    """HTML 뷰에서 CSRF 쿠키 + 토큰 추출"""
    view_resp = await client.get("/api/v1/view/files/sample", cookies=cookies)
    csrf_cookie = view_resp.cookies.get("csrf_token", "")
    all_cookies = {**cookies}
    if csrf_cookie:
        all_cookies["csrf_token"] = csrf_cookie
    csrf_token = ""
    match = re.search(r'name="csrf-token"\s+content="([^"]+)"', view_resp.text)
    if match:
        csrf_token = match.group(1)
    return all_cookies, csrf_token


class TestEditorLockAPI:
    """Lock 관리 API 테스트"""

    @pytest.mark.asyncio
    async def test_acquire_lock(self, app_client: AsyncClient) -> None:
        cookies = await login_and_get_cookies(app_client)
        all_cookies, csrf_token = await _get_csrf(app_client, cookies)

        resp = await app_client.post(
            "/api/v1/editor/lock/sample/0",
            cookies=all_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_acquire_lock_unauthorized(self, app_client: AsyncClient) -> None:
        resp = await app_client.post("/api/v1/editor/lock/sample/0")
        assert resp.status_code == 401


class TestEditorDataAPI:
    """Data CRUD API 테스트"""

    @pytest.mark.asyncio
    async def test_get_data(self, app_client: AsyncClient) -> None:
        resp = await app_client.get("/api/v1/editor/data/sample/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_idx"] == 0
        assert data["file_id"] == "sample"
        assert "content" in data
        assert data["content"]["question"] == "문제 1"

    @pytest.mark.asyncio
    async def test_get_data_not_found(self, app_client: AsyncClient) -> None:
        resp = await app_client.get("/api/v1/editor/data/sample/999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_save_data_full_flow(self, app_client: AsyncClient) -> None:
        """전체 저장 플로우: 로그인 → Lock 획득 → 저장 (changes dict 방식)"""
        cookies = await login_and_get_cookies(app_client)
        all_cookies, csrf_token = await _get_csrf(app_client, cookies)
        headers = {"X-CSRF-Token": csrf_token}

        lock_resp = await app_client.post(
            "/api/v1/editor/lock/sample/0",
            cookies=all_cookies,
            headers=headers,
        )
        assert lock_resp.status_code == 200

        save_resp = await app_client.put(
            "/api/v1/editor/data/sample/0",
            json={
                "changes": {"content": {"question": "수정됨", "answer": "답변 수정"}},
                "version": 1,
            },
            cookies=all_cookies,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert save_resp.status_code == 200
        save_data = save_resp.json()
        assert save_data["success"] is True
        assert save_data["data"]["version"] == 2

    @pytest.mark.asyncio
    async def test_save_without_lock(self, app_client: AsyncClient) -> None:
        """Lock 없이 저장 → 403"""
        cookies = await login_and_get_cookies(app_client)
        all_cookies, csrf_token = await _get_csrf(app_client, cookies)

        resp = await app_client.put(
            "/api/v1/editor/data/sample/1",
            json={
                "changes": {"content": {"question": "무단 수정"}},
                "version": 1,
            },
            cookies=all_cookies,
            headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_save_version_conflict(self, app_client: AsyncClient) -> None:
        """Optimistic Locking: 잘못된 버전 → 409"""
        cookies = await login_and_get_cookies(app_client)
        all_cookies, csrf_token = await _get_csrf(app_client, cookies)
        headers = {"X-CSRF-Token": csrf_token}

        await app_client.post(
            "/api/v1/editor/lock/sample/0",
            cookies=all_cookies,
            headers=headers,
        )

        resp = await app_client.put(
            "/api/v1/editor/data/sample/0",
            json={
                "changes": {"content": {"question": "충돌"}},
                "version": 999,
            },
            cookies=all_cookies,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_save_type_mismatch_rejected(self, app_client: AsyncClient) -> None:
        """타입 불일치 저장 → 422"""
        cookies = await login_and_get_cookies(app_client)
        all_cookies, csrf_token = await _get_csrf(app_client, cookies)
        headers = {"X-CSRF-Token": csrf_token}

        await app_client.post(
            "/api/v1/editor/lock/sample/0",
            cookies=all_cookies,
            headers=headers,
        )

        resp = await app_client.put(
            "/api/v1/editor/data/sample/0",
            json={
                "changes": {"content": "dict인데 문자열로 변환 시도"},
                "version": 1,
            },
            cookies=all_cookies,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert resp.status_code == 422
