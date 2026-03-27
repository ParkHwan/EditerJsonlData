"""WebSocket 엔드포인트 — 실시간 파일 Lock 상태 (Phase 4 → Phase 14: 파일 단위)"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.deps import get_auth_service, get_lock_service
from app.core.config import settings
from app.services.auth_service import AuthService
from app.services.lock_service import LockService
from app.services.metadata_service import metadata_service
from app.services.websocket_manager import ws_manager

router = APIRouter()


@router.websocket("/ws/lock-status/{file_id}")
async def lock_status_ws(
    websocket: WebSocket,
    file_id: str,
    auth_service: AuthService = Depends(get_auth_service),
    lock_service: LockService = Depends(get_lock_service),
) -> None:
    """file_id에 대한 파일 Lock 상태 실시간 구독. 세션 쿠키로 인증."""
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
        lock_info = await lock_service.get_lock_info(file_id)
        if lock_info:
            display_name = await metadata_service.get_display_name(
                lock_info["user_id"]
            )
            lock_info["display_name"] = display_name or lock_info["user_id"]
        await websocket.send_json({"type": "init", "lock": lock_info})

        while True:
            try:
                msg: Any = await asyncio.wait_for(websocket.receive_json(), timeout=45.0)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    break
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        ws_manager.disconnect(file_id, websocket)
