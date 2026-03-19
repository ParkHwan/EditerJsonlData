"""AuditService 단위 테스트

- 감사 로그 기록 / 조회
- 필터링 (user_id, action)
- 오래된 로그 정리
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.audit_service import AuditService


def _make_request(ip: str = "192.168.1.1") -> MagicMock:
    request = MagicMock()
    request.headers = {"user-agent": "TestBot/1.0"}
    request.client = MagicMock()
    request.client.host = ip
    return request


class TestAuditService:
    """감사 로그 서비스 테스트"""

    @pytest.mark.asyncio
    async def test_log_creates_file(self, tmp_data_dir: Path) -> None:
        """로그 기록 시 파일 생성"""
        svc = AuditService()
        svc.audit_dir = tmp_data_dir / "audit"
        svc.audit_dir.mkdir(exist_ok=True)

        await svc.log(
            action="login",
            request=_make_request(),
            user_id="user1",
            display_name="유저1",
        )

        log_files = list(svc.audit_dir.glob("audit_*.jsonl"))
        assert len(log_files) == 1

        with open(log_files[0], "r") as f:
            entry = json.loads(f.readline())

        assert entry["user_id"] == "user1"
        assert entry["action"] == "login"
        assert entry["ip_address"] == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_log_multiple_entries(self, tmp_data_dir: Path) -> None:
        """복수 로그 기록"""
        svc = AuditService()
        svc.audit_dir = tmp_data_dir / "audit"
        svc.audit_dir.mkdir(exist_ok=True)

        for action in ("login", "view", "edit_start", "edit_save", "logout"):
            await svc.log(
                action=action,  # type: ignore[arg-type]
                request=_make_request(),
                user_id="user1",
                display_name="유저1",
                file_id="sample",
            )

        logs = await svc.get_logs()
        assert len(logs) == 5

    @pytest.mark.asyncio
    async def test_get_logs_filter_by_user(self, tmp_data_dir: Path) -> None:
        """사용자별 필터링"""
        svc = AuditService()
        svc.audit_dir = tmp_data_dir / "audit"
        svc.audit_dir.mkdir(exist_ok=True)

        await svc.log(action="login", request=_make_request(), user_id="user_a", display_name="A")
        await svc.log(action="login", request=_make_request(), user_id="user_b", display_name="B")
        await svc.log(action="view", request=_make_request(), user_id="user_a", display_name="A")

        logs_a = await svc.get_logs(user_id="user_a")
        assert len(logs_a) == 2

        logs_b = await svc.get_logs(user_id="user_b")
        assert len(logs_b) == 1

    @pytest.mark.asyncio
    async def test_get_logs_filter_by_action(self, tmp_data_dir: Path) -> None:
        """행위별 필터링"""
        svc = AuditService()
        svc.audit_dir = tmp_data_dir / "audit"
        svc.audit_dir.mkdir(exist_ok=True)

        await svc.log(action="login", request=_make_request(), user_id="u1", display_name="U")
        await svc.log(action="view", request=_make_request(), user_id="u1", display_name="U")
        await svc.log(action="view", request=_make_request(), user_id="u1", display_name="U")

        logs = await svc.get_logs(action="view")
        assert len(logs) == 2

    @pytest.mark.asyncio
    async def test_get_logs_empty_date(self, tmp_data_dir: Path) -> None:
        """존재하지 않는 날짜 → 빈 리스트"""
        svc = AuditService()
        svc.audit_dir = tmp_data_dir / "audit"
        svc.audit_dir.mkdir(exist_ok=True)

        logs = await svc.get_logs(date="20200101")
        assert logs == []

    @pytest.mark.asyncio
    async def test_log_includes_metadata(self, tmp_data_dir: Path) -> None:
        """메타데이터 포함 확인"""
        svc = AuditService()
        svc.audit_dir = tmp_data_dir / "audit"
        svc.audit_dir.mkdir(exist_ok=True)

        await svc.log(
            action="edit_save",
            request=_make_request(),
            user_id="u1",
            display_name="U",
            file_id="test_file",
            row_idx=42,
            changes={"content": {"q": "updated"}},
            metadata={"page": 3},
        )

        logs = await svc.get_logs()
        assert len(logs) == 1
        assert logs[0]["file_id"] == "test_file"
        assert logs[0]["row_idx"] == 42
        assert logs[0]["changes"]["content"]["q"] == "updated"
        assert logs[0]["metadata"]["page"] == 3
