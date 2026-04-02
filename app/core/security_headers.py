"""보안 헤더 미들웨어 (Phase 5)

Content-Security-Policy, X-Content-Type-Options 등
응답에 보안 관련 HTTP 헤더를 일괄 추가한다.

순수 ASGI 미들웨어로 구현하여 WebSocket 연결에 간섭하지 않는다.
(BaseHTTPMiddleware는 Gunicorn+UvicornWorker 환경에서
 WebSocket handshake를 가로채 403을 유발할 수 있음)
"""

from __future__ import annotations

from typing import Any, Callable

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

_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"content-security-policy", CSP_POLICY.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


class SecurityHeadersMiddleware:
    """순수 ASGI 미들웨어 — HTTP 응답에만 보안 헤더를 주입하고 WebSocket은 통과시킨다."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Any],
        send: Callable[..., Any],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_SECURITY_HEADERS)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
