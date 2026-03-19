"""WebSocket ConnectionManager 테스트 (Phase 5)

- 연결 / 해제
- broadcast 전달 확인
- dead connection 자동 제거
- file_id별 격리
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.websocket_manager import ConnectionManager


def _make_ws(accept_ok: bool = True, send_ok: bool = True) -> MagicMock:
    """Mock WebSocket 생성"""
    ws = MagicMock()
    ws.accept = AsyncMock()
    if send_ok:
        ws.send_json = AsyncMock()
    else:
        ws.send_json = AsyncMock(side_effect=Exception("connection closed"))
    return ws


class TestConnectionManager:
    """WebSocket 연결 풀 관리"""

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self) -> None:
        mgr = ConnectionManager()
        ws = _make_ws()

        await mgr.connect("file1", ws)
        ws.accept.assert_called_once()

        mgr.disconnect("file1", ws)
        assert "file1" not in mgr._connections or ws not in mgr._connections.get("file1", [])

    @pytest.mark.asyncio
    async def test_broadcast_to_all_clients(self) -> None:
        """같은 file_id의 모든 클라이언트에 메시지 전송"""
        mgr = ConnectionManager()
        ws1 = _make_ws()
        ws2 = _make_ws()

        await mgr.connect("file1", ws1)
        await mgr.connect("file1", ws2)

        data = {"type": "lock_change", "action": "acquired", "row_idx": 0}
        await mgr.broadcast("file1", data)

        ws1.send_json.assert_called_once_with(data)
        ws2.send_json.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_broadcast_isolates_file_ids(self) -> None:
        """다른 file_id의 클라이언트에는 메시지 미전송"""
        mgr = ConnectionManager()
        ws_file1 = _make_ws()
        ws_file2 = _make_ws()

        await mgr.connect("file1", ws_file1)
        await mgr.connect("file2", ws_file2)

        await mgr.broadcast("file1", {"type": "test"})

        ws_file1.send_json.assert_called_once()
        ws_file2.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self) -> None:
        """전송 실패 연결 자동 제거"""
        mgr = ConnectionManager()
        ws_alive = _make_ws(send_ok=True)
        ws_dead = _make_ws(send_ok=False)

        await mgr.connect("file1", ws_alive)
        await mgr.connect("file1", ws_dead)

        await mgr.broadcast("file1", {"type": "test"})

        ws_alive.send_json.assert_called_once()
        remaining = mgr._connections.get("file1", [])
        assert ws_dead not in remaining
        assert ws_alive in remaining

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self) -> None:
        """연결 없는 file_id에 broadcast → 에러 없이 무시"""
        mgr = ConnectionManager()
        await mgr.broadcast("nonexistent", {"type": "test"})

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self) -> None:
        """연결되지 않은 ws disconnect → 에러 없이 무시"""
        mgr = ConnectionManager()
        ws = _make_ws()
        mgr.disconnect("nonexistent_file", ws)

    @pytest.mark.asyncio
    async def test_disconnect_cleans_empty_pool(self) -> None:
        """마지막 연결 해제 시 빈 리스트 정리"""
        mgr = ConnectionManager()
        ws = _make_ws()

        await mgr.connect("file1", ws)
        mgr.disconnect("file1", ws)

        assert "file1" not in mgr._connections

    @pytest.mark.asyncio
    async def test_all_dead_connections_cleans_pool(self) -> None:
        """모든 연결이 dead인 경우 broadcast 후 풀 정리"""
        mgr = ConnectionManager()
        ws1 = _make_ws(send_ok=False)
        ws2 = _make_ws(send_ok=False)

        await mgr.connect("file1", ws1)
        await mgr.connect("file1", ws2)

        await mgr.broadcast("file1", {"type": "test"})

        remaining = mgr._connections.get("file1", [])
        assert len(remaining) == 0
