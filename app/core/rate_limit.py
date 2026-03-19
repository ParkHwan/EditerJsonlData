"""Rate Limiting 설정 (slowapi)

- IP 기반 기본 제한
- 인증된 사용자는 user_id 기반 제한
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


def _get_identifier(request: Request) -> str:
    """인증된 사용자 ID 또는 IP로 Rate Limit 키 생성"""
    # 세션 쿠키에서 user_id를 직접 추출하기 어려우므로,
    # request.state에 미들웨어/의존성에서 설정한 값을 참조
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict):
        return f"user:{user.get('user_id', get_remote_address(request))}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_get_identifier,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
)
