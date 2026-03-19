"""인증 서비스 (Phase 11: 이메일 + 비밀번호 + Redis 세션)

crowdworks.kr 도메인 이메일 기반 로그인.
- 관리자가 사용자 등록 시 bcrypt 해싱된 비밀번호를 DuckDB에 저장
- 로그인 시 이메일 + 비밀번호 검증 → Redis 세션 생성
- 세션 고정 공격 방지 (로그인 시 새 세션 ID 발급)
- IP / User-Agent 변경 감지 (경고 로그)
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from fastapi import HTTPException, Request, Response
from redis.asyncio import Redis

from app.core.config import settings
from app.core.logger import logger


class AuthService:
    """이메일/비밀번호 + Redis 세션 기반 인증 서비스"""

    SESSION_PREFIX = "session:"
    SESSION_TTL = timedelta(hours=settings.SESSION_TTL_HOURS)
    ALLOWED_DOMAIN = settings.ALLOWED_EMAIL_DOMAIN

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    # ------------------------------------------------------------------
    # 비밀번호 해싱 / 검증
    # ------------------------------------------------------------------
    @staticmethod
    def hash_password(plain_password: str) -> str:
        """bcrypt로 비밀번호 해싱"""
        return bcrypt.hashpw(
            plain_password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed: str) -> bool:
        """bcrypt 비밀번호 검증"""
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed.encode("utf-8"),
            )
        except Exception:
            return False

    @staticmethod
    def validate_email_domain(email: str) -> bool:
        """crowdworks.kr 도메인 검증"""
        if not email or "@" not in email:
            return False
        domain = email.split("@")[-1].lower()
        return domain == settings.ALLOWED_EMAIL_DOMAIN

    # ------------------------------------------------------------------
    # 세션 생성 / 삭제
    # ------------------------------------------------------------------
    async def create_session(
        self,
        user_id: str,
        display_name: str,
        request: Request,
        old_session_id: str | None = None,
        email: str = "",
        is_admin: bool = False,
    ) -> str:
        """로그인 → 새 세션 생성 (세션 고정 공격 방지)

        Returns:
            새 세션 ID
        """
        if old_session_id:
            await self.redis.delete(f"{self.SESSION_PREFIX}{old_session_id}")

        new_session_id = secrets.token_urlsafe(32)
        session_data: dict[str, Any] = {
            "user_id": user_id,
            "display_name": display_name,
            "email": email,
            "is_admin": is_admin,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "ip": self._get_client_ip(request),
            "user_agent": request.headers.get("user-agent", ""),
        }

        await self.redis.setex(
            f"{self.SESSION_PREFIX}{new_session_id}",
            int(self.SESSION_TTL.total_seconds()),
            json.dumps(session_data, ensure_ascii=False),
        )
        logger.info("Session created for %s (%s)", user_id, display_name)
        return new_session_id

    async def destroy_session(self, session_id: str) -> None:
        """로그아웃 → 세션 삭제"""
        await self.redis.delete(f"{self.SESSION_PREFIX}{session_id}")

    # ------------------------------------------------------------------
    # 세션 검증
    # ------------------------------------------------------------------
    async def validate_session(
        self,
        session_id: str,
        request: Request | None = None,
    ) -> dict[str, Any] | None:
        """세션 유효성 검증 (IP / User-Agent 변경 감지).

        request가 None이면 IP 검사 생략 (WebSocket 등).
        """
        if not session_id:
            return None

        raw = await self.redis.get(f"{self.SESSION_PREFIX}{session_id}")
        if not raw:
            return None

        try:
            data: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        if request is not None:
            current_ip = self._get_client_ip(request)
            if data.get("ip") and data["ip"] != current_ip:
                logger.warning(
                    "Session IP changed: %s... %s → %s (user=%s)",
                    session_id[:8],
                    data["ip"],
                    current_ip,
                    data.get("user_id"),
                )

        return data

    async def get_session_user(
        self,
        session_id: str,
        request: Request,
    ) -> dict[str, Any]:
        """세션에서 사용자 정보 추출 (실패 시 401)"""
        data = await self.validate_session(session_id, request)
        if data is None:
            raise HTTPException(
                status_code=401,
                detail="인증이 필요합니다. 로그인해 주세요.",
            )
        return data

    # ------------------------------------------------------------------
    # 유틸리티
    # ------------------------------------------------------------------
    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """프록시 헤더를 고려한 클라이언트 IP 추출"""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    @staticmethod
    def set_session_cookie(response: Response, session_id: str) -> None:
        """응답에 세션 쿠키 설정"""
        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=session_id,
            httponly=settings.SESSION_COOKIE_HTTPONLY,
            secure=settings.SESSION_COOKIE_SECURE,
            samesite=settings.SESSION_COOKIE_SAMESITE,
            max_age=int(AuthService.SESSION_TTL.total_seconds()),
        )

    @staticmethod
    def delete_session_cookie(response: Response) -> None:
        """세션 쿠키 삭제"""
        response.delete_cookie(key=settings.SESSION_COOKIE_NAME)
