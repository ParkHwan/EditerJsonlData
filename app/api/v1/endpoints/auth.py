"""인증 엔드포인트 (Phase 11: 이메일/비밀번호 + 관리자 사용자 관리)

crowdworks.kr 이메일 + 비밀번호 기반 로그인.
- POST /login   → 이메일/비밀번호 검증 + 세션 생성
- POST /logout  → 세션 삭제 + 쿠키 삭제
- GET  /me      → 현재 사용자 정보
- POST /users/register → 관리자: 신규 사용자 등록
- GET  /users          → 관리자: 사용자 목록
- PATCH /users/{user_id}/toggle → 관리자: 활성/비활성 토글
- PATCH /users/{user_id}/password → 관리자: 비밀번호 재설정
- DELETE /users/{user_id} → 관리자: 사용자 삭제
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import get_auth_service, get_current_user
from app.core.config import settings
from app.core.logger import logger
from app.services.audit_service import audit_service
from app.services.auth_service import AuthService
from app.services.metadata_service import metadata_service

router = APIRouter()


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=100, description="회사 이메일")
    password: str = Field(..., min_length=1, max_length=200, description="비밀번호")


class LoginResponse(BaseModel):
    success: bool
    message: str
    user_id: str
    display_name: str


class RegisterUserRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=100, description="crowdworks.kr 이메일")
    display_name: str = Field(..., min_length=1, max_length=100, description="표시 이름")
    password: str = Field(..., min_length=4, max_length=200, description="초기 비밀번호")
    is_admin: bool = Field(default=False, description="관리자 여부")


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=4, max_length=200, description="새 비밀번호")


class UserInfoResponse(BaseModel):
    user_id: str
    display_name: str
    email: str
    is_admin: bool
    created_at: str
    ip: str


# ---------------------------------------------------------------------------
# 관리자 권한 검증 헬퍼
# ---------------------------------------------------------------------------
async def _require_admin(current_user: dict[str, Any]) -> None:
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")


# ---------------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------------
@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
    session_id: str | None = Cookie(None, alias=settings.SESSION_COOKIE_NAME),
) -> LoginResponse:
    """이메일 + 비밀번호 로그인 → Redis 세션 생성"""
    email = body.email.strip().lower()

    if not AuthService.validate_email_domain(email):
        raise HTTPException(
            status_code=403,
            detail=f"@{settings.ALLOWED_EMAIL_DOMAIN} 이메일만 로그인할 수 있습니다.",
        )

    user = await metadata_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=401, detail="등록되지 않은 이메일입니다. 관리자에게 문의하세요.")

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다. 관리자에게 문의하세요.")

    if not AuthService.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")

    new_session_id = await auth_service.create_session(
        user_id=user["user_id"],
        display_name=user["display_name"],
        request=request,
        old_session_id=session_id,
        email=email,
        is_admin=user.get("is_admin", False),
    )
    AuthService.set_session_cookie(response, new_session_id)

    await audit_service.log(
        action="login",
        request=request,
        user_id=user["user_id"],
        display_name=user["display_name"],
    )

    try:
        await metadata_service.increment_login_count(user["user_id"])
    except Exception as e:
        logger.warning("DuckDB login count update failed (non-blocking): %s", e)

    return LoginResponse(
        success=True,
        message="로그인 성공",
        user_id=user["user_id"],
        display_name=user["display_name"],
    )


# ---------------------------------------------------------------------------
# 로그아웃
# ---------------------------------------------------------------------------
@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
    session_id: str | None = Cookie(None, alias=settings.SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """로그아웃 → 세션 삭제 + 쿠키 삭제"""
    user_id = ""
    display_name = ""

    if session_id:
        user_data = await auth_service.validate_session(session_id, request)
        if user_data:
            user_id = user_data.get("user_id", "")
            display_name = user_data.get("display_name", "")
        await auth_service.destroy_session(session_id)

    AuthService.delete_session_cookie(response)

    await audit_service.log(
        action="logout",
        request=request,
        user_id=user_id,
        display_name=display_name,
    )

    return {"success": True, "message": "로그아웃 완료"}


# ---------------------------------------------------------------------------
# 현재 사용자 정보
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserInfoResponse)
async def get_me(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> UserInfoResponse:
    """현재 로그인된 사용자 정보 반환"""
    return UserInfoResponse(
        user_id=current_user["user_id"],
        display_name=current_user["display_name"],
        email=current_user.get("email", ""),
        is_admin=current_user.get("is_admin", False),
        created_at=current_user.get("created_at", ""),
        ip=current_user.get("ip", ""),
    )


# ---------------------------------------------------------------------------
# 관리자: 사용자 등록
# ---------------------------------------------------------------------------
@router.post("/users/register")
async def register_user(
    body: RegisterUserRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """관리자가 새 사용자를 등록"""
    await _require_admin(current_user)

    email = body.email.strip().lower()
    if not AuthService.validate_email_domain(email):
        raise HTTPException(
            status_code=400,
            detail=f"@{settings.ALLOWED_EMAIL_DOMAIN} 이메일만 등록할 수 있습니다.",
        )

    existing = await metadata_service.get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail=f"이미 등록된 이메일입니다: {email}")

    user_id = email.split("@")[0]
    password_hash = AuthService.hash_password(body.password)

    await metadata_service.register_user(
        user_id=user_id,
        display_name=body.display_name.strip(),
        email=email,
        password_hash=password_hash,
        is_admin=body.is_admin,
    )

    logger.info(
        "User registered by admin %s: %s (%s)",
        current_user["user_id"],
        user_id,
        email,
    )
    return {
        "success": True,
        "message": f"사용자 등록 완료: {body.display_name} ({email})",
        "user_id": user_id,
    }


# ---------------------------------------------------------------------------
# 관리자: 사용자 목록
# ---------------------------------------------------------------------------
@router.get("/users")
async def list_users(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """관리자가 전체 사용자 목록 조회"""
    await _require_admin(current_user)
    users = await metadata_service.list_users()
    return {"users": users, "total": len(users)}


# ---------------------------------------------------------------------------
# 관리자: 사용자 활성/비활성 토글
# ---------------------------------------------------------------------------
@router.patch("/users/{user_id}/toggle")
async def toggle_user_active(
    user_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """사용자 활성/비활성 상태 토글"""
    await _require_admin(current_user)

    users = await metadata_service.list_users()
    target = next((u for u in users if u["user_id"] == user_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    new_state = not target["is_active"]
    await metadata_service.update_user_active(user_id, new_state)

    status_text = "활성화" if new_state else "비활성화"
    return {"success": True, "message": f"{target['display_name']} ({user_id}) {status_text}됨"}


# ---------------------------------------------------------------------------
# 관리자: 비밀번호 재설정
# ---------------------------------------------------------------------------
@router.patch("/users/{user_id}/password")
async def reset_user_password(
    user_id: str,
    body: ResetPasswordRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """사용자 비밀번호 재설정 (관리자)"""
    await _require_admin(current_user)

    password_hash = AuthService.hash_password(body.new_password)
    await metadata_service.update_user_password(user_id, password_hash)

    return {"success": True, "message": f"{user_id} 비밀번호 재설정 완료"}


# ---------------------------------------------------------------------------
# 관리자: 사용자 삭제
# ---------------------------------------------------------------------------
@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """사용자 삭제 (관리자)"""
    await _require_admin(current_user)

    if user_id == current_user["user_id"]:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")

    await metadata_service.delete_user(user_id)
    return {"success": True, "message": f"{user_id} 삭제 완료"}
