"""WebSocket 엔드포인트 — 실시간 Lock 상태 (Phase 4)"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.deps import get_auth_service, get_lock_service
from app.core.config import settings
from app.services.auth_service import AuthService
from app.services.lock_service import LockService
from app.services.websocket_manager import ws_manager

router = APIRouter()


@router.websocket("/ws/lock-status/{file_id}")
async def lock_status_ws(
    websocket: WebSocket,
    file_id: str,
    auth_service: AuthService = Depends(get_auth_service),
    lock_service: LockService = Depends(get_lock_service),
) -> None:
    """file_id에 대한 Lock 상태 실시간 구독. 세션 쿠키로 인증."""
    # 경로 순회 방지 (file_id는 파일명만 허용)
    if "/" in file_id or ".." in file_id or not file_id.strip():
        await websocket.close(code=4000)
        return

    session_id = websocket.cookies.get(settings.SESSION_COOKIE_NAME) or ""
    user = await auth_service.validate_session(session_id, None)
    if not user:
        await websocket.close(code=4401)
        return

    await ws_manager.connect(file_id, websocket)

    try:
        # 초기 Lock 목록 전송
        locks = await lock_service.get_all_locks(file_id)
        await websocket.send_json({"type": "init", "locks": locks})

        # Keepalive + 수신 루프
        while True:
            try:
                msg: Any = await asyncio.wait_for(websocket.receive_json(), timeout=45.0)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # timeout 시 keepalive 전송 — 연결 해제된 상태면 예외를 상위로 전파
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    break
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        ws_manager.disconnect(file_id, websocket)
