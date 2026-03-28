"""Rate Limiting 설정 (slowapi)

- 세션 쿠키 기반 사용자 식별 (우선)
- X-Forwarded-For 헤더로 실제 클라이언트 IP 추출 (프록시 환경 지원)
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter

from app.core.config import settings


def _get_real_ip(request: Request) -> str:
    """X-Forwarded-For를 고려한 클라이언트 IP 추출"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def _get_identifier(request: Request) -> str:
    """세션 쿠키 → IP 순서로 Rate Limit 키 생성"""
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    if session_id:
        return f"session:{session_id[:16]}"
    return f"ip:{_get_real_ip(request)}"


limiter = Limiter(
    key_func=_get_identifier,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
)
