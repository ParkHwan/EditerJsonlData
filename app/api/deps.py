"""공통 의존성 (Dependency Injection) — Phase 3 업데이트

- AuthService, LockService, DraftService DI
- get_current_user / get_optional_user (세션 쿠키 기반)
"""

from __future__ import annotations

from typing import Any

from fastapi import Cookie, Depends, Request
from redis.asyncio import Redis

from app.core.config import settings
from app.core.pending_tasks import PendingTaskTracker, pending_tracker
from app.db.redis_client import get_redis_client
from app.services.auth_service import AuthService
from app.services.draft_service import DraftService
from app.services.lock_service import LockService


def get_pending_tracker() -> PendingTaskTracker:
    """Graceful Shutdown용 PendingTaskTracker (Phase 4)"""
    return pending_tracker


async def get_auth_service(
    redis: Redis = Depends(get_redis_client),
) -> AuthService:
    """AuthService DI"""
    return AuthService(redis)


async def get_lock_service(
    redis: Redis = Depends(get_redis_client),
) -> LockService:
    """LockService DI"""
    return LockService(redis)


async def get_draft_service(
    redis: Redis = Depends(get_redis_client),
) -> DraftService:
    """DraftService DI"""
    return DraftService(redis)


async def get_current_user(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    session_id: str | None = Cookie(None, alias=settings.SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """현재 인증된 사용자 정보 반환 (세션 쿠키 기반)

    Returns:
        {"user_id": ..., "display_name": ..., ...}
    """
    return await auth_service.get_session_user(
        session_id=session_id or "",
        request=request,
    )


async def get_optional_user(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    session_id: str | None = Cookie(None, alias=settings.SESSION_COOKIE_NAME),
) -> dict[str, Any] | None:
    """인증 선택적 (비로그인 시 None 반환)"""
    if not session_id:
        return None
    return await auth_service.validate_session(session_id, request)
