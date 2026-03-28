"""글로벌 예외 핸들러 (Phase 3: CSRF 에러 추가)"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi_csrf_protect.exceptions import CsrfProtectError
from slowapi.errors import RateLimitExceeded


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Rate Limit 초과 시 429 응답 (Retry-After 헤더 포함)"""
    retry_seconds = 5
    return JSONResponse(
        status_code=429,
        content={
            "detail": "요청 횟수가 제한을 초과했습니다. 잠시 후 다시 시도해 주세요.",
            "retry_after": retry_seconds,
        },
        headers={"Retry-After": str(retry_seconds)},
    )


def csrf_protect_exception_handler(
    request: Request, exc: CsrfProtectError
) -> JSONResponse:
    """CSRF 토큰 검증 실패 시 403 응답"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": "CSRF 토큰이 유효하지 않습니다. 페이지를 새로고침한 후 다시 시도해 주세요.",
            "error": exc.message,
        },
    )
