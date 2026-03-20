"""보안 헤더 미들웨어 (Phase 5)

Content-Security-Policy, X-Content-Type-Options 등
응답에 보안 관련 HTTP 헤더를 일괄 추가한다.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CSP_DIRECTIVES = {
    "default-src": "'self'",
    "script-src": "'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src": "'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "img-src": "'self' data:",
    "font-src": "'self' data: https://cdn.jsdelivr.net",
    "connect-src": "'self' ws: wss:",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
}

CSP_POLICY = "; ".join(f"{k} {v}" for k, v in CSP_DIRECTIVES.items())


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response
