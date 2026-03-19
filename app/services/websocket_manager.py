"""WebSocket 연결 관리 — file_id별 실시간 Lock 상태 브로드캐스트 (Phase 4)"""

from __future__ import annotations

from fastapi import WebSocket


class ConnectionManager:
    """file_id별 WebSocket 연결 풀 관리. Lock 변경 시 해당 file_id 구독 클라이언트에만 전송."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, file_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(file_id, []).append(ws)

    def disconnect(self, file_id: str, ws: WebSocket) -> None:
        if file_id in self._connections:
            self._connections[file_id] = [c for c in self._connections[file_id] if c != ws]
            if not self._connections[file_id]:
                del self._connections[file_id]

    async def broadcast(self, file_id: str, data: dict) -> None:
        """file_id에 연결된 모든 클라이언트에 JSON 메시지 전송. 전송 실패 연결은 제거."""
        if file_id not in self._connections:
            return
        # 순회 중 리스트 변경 방지를 위해 복사본 사용
        snapshot = list(self._connections[file_id])
        dead: list[WebSocket] = []
        for conn in snapshot:
            try:
                await conn.send_json(data)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(file_id, conn)


ws_manager = ConnectionManager()
