"""CSRF Protection 설정 (Phase 3)

fastapi-csrf-protect의 Double Submit Cookie 패턴을 활용한다.
- HTML 뷰 렌더링 시 CSRF 토큰 생성 + 쿠키 설정
- 상태 변경 요청 (POST/PUT/DELETE) 시 X-CSRF-Token 헤더 검증
"""

from __future__ import annotations

from fastapi_csrf_protect import CsrfProtect
from pydantic_settings import BaseSettings

from app.core.config import settings


class CsrfSettings(BaseSettings):
    """CSRF 보호 설정"""

    secret_key: str = settings.SECRET_KEY
    cookie_samesite: str = settings.SESSION_COOKIE_SAMESITE
    cookie_secure: bool = settings.SESSION_COOKIE_SECURE
    header_name: str = "X-CSRF-Token"
    cookie_key: str = "csrf_token"
    token_location: str = "header"


@CsrfProtect.load_config
def get_csrf_config() -> CsrfSettings:
    return CsrfSettings()
