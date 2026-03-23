"""파일 목록 및 뷰어 엔드포인트 (Phase 3: CSRF + Audit 통합)

HTML 템플릿을 렌더링하는 뷰 레이어.
- CSRF 토큰 생성 (Double Submit Cookie)
- 세션 인증이 없으면 로그인 페이지로 리다이렉트
- Audit 로그: view, download
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_csrf_protect import CsrfProtect

from app.api.deps import get_current_user, get_optional_user
from app.core.config import settings
from app.core.rate_limit import limiter
from app.services.audit_service import audit_service
from app.services.file_service import file_service
from app.services.gcs_edit_service import gcs_edit_service


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")




# ---------------------------------------------------------------------------
# 로그인 페이지 (CSRF 토큰 포함)
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: str = "",
    csrf_protect: CsrfProtect = Depends(),
):
    """로그인 페이지 (이메일 + 비밀번호)"""
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request,
        "login.html",
        {
            "csrf_token": csrf_token,
            "api_prefix": settings.API_V1_STR,
            "error": error,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


# ---------------------------------------------------------------------------
# 관리자: 사용자 관리 페이지
# ---------------------------------------------------------------------------
@router.get("/admin/users", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def admin_users_page(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] | None = Depends(get_optional_user),
):
    """관리자 사용자 관리 페이지"""
    if current_user is None:
        return RedirectResponse(url=f"{settings.API_V1_STR}/view/login")

    if not current_user.get("is_admin", False):
        return RedirectResponse(url=f"{settings.API_V1_STR}/view/files")

    from app.services.metadata_service import metadata_service

    users = await metadata_service.list_users()

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "users": users,
            "current_user": current_user,
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


# ---------------------------------------------------------------------------
# 파일 목록 페이지
# ---------------------------------------------------------------------------
@router.get("/files", response_class=HTMLResponse)
@limiter.limit("100/minute")
async def list_files(
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] | None = Depends(get_optional_user),
):
    """TASK 선택 페이지 (파일 목록 진입점)"""
    if current_user is None:
        return RedirectResponse(url=f"{settings.API_V1_STR}/view/login")

    tasks = [
        {"id": tid, "name": info["name"], "prefix": info["prefix"]}
        for tid, info in settings.GCS_TASKS.items()
    ]

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "tasks": tasks,
            "current_user": current_user,
            "csrf_token": csrf_token,
            "bucket_name": settings.GCS_BUCKET_NAME,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


# ---------------------------------------------------------------------------
# 파일 뷰어 (아이템 카드 렌더링)
# ---------------------------------------------------------------------------
@router.get("/files/{file_id}", response_class=HTMLResponse)
@limiter.limit("100/minute")
async def view_file(
    request: Request,
    file_id: str,
    gcs_date: str = "",
    mode: str = "",
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] | None = Depends(get_optional_user),
):
    """JSONL 파일의 Master-Detail 뷰 (좌측 data_id 목록 + 우측 상세)

    mode=gcs: Redis working copy에서 데이터 조회 (GCS 직접 편집 모드)
    mode=""  : 로컬 파일 기반 (기존 방식)
    """
    if current_user is None:
        return RedirectResponse(url=f"{settings.API_V1_STR}/view/login")

    safe_id = Path(file_id).name
    is_gcs_mode = mode == "gcs" and await gcs_edit_service.is_loaded(safe_id)

    if is_gcs_mode:
        data_id_list = await gcs_edit_service.get_data_id_list(safe_id)
        meta = await gcs_edit_service.get_meta(safe_id)
        date_str = gcs_date.strip() or (meta.get("date_str", "") if meta else "")
    else:
        file_path = Path(settings.DATA_DIR) / f"{safe_id}.jsonl"
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

        from app.services.gcs_service import gcs_service

        date_str = gcs_date.strip()
        if not date_str:
            date_str = gcs_service.get_date_str_for_file(safe_id) or ""

        data_id_list = await file_service.get_data_id_list(safe_id)

    total_items = len(data_id_list)

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()

    await audit_service.log(
        action="view",
        request=request,
        user_id=current_user["user_id"],
        display_name=current_user.get("display_name", ""),
        file_id=safe_id,
        metadata={"mode": "gcs_redis" if is_gcs_mode else "local"},
    )

    response = templates.TemplateResponse(
        request,
        "editor.html",
        {
            "file_id": safe_id,
            "file_name": f"{safe_id}.jsonl",
            "total_items": total_items,
            "data_id_list": data_id_list,
            "current_user": current_user,
            "csrf_token": csrf_token,
            "auto_save_interval": settings.DRAFT_AUTO_SAVE_INTERVAL,
            "gcs_date": date_str,
            "edit_mode": "gcs" if is_gcs_mode else "local",
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


# ---------------------------------------------------------------------------
# 파일 다운로드
# ---------------------------------------------------------------------------
@router.get("/files/{file_id}/download")
@limiter.limit("10/minute")
async def download_file(
    request: Request,
    file_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """JSONL 파일 다운로드 (인증 필수)"""
    safe_id = Path(file_id).name
    file_path = Path(settings.DATA_DIR) / f"{safe_id}.jsonl"

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    # Audit 로그: download
    await audit_service.log(
        action="download",
        request=request,
        user_id=current_user["user_id"],
        display_name=current_user.get("display_name", ""),
        file_id=safe_id,
    )

    return FileResponse(
        path=str(file_path),
        filename=f"{safe_id}.jsonl",
        media_type="application/json",
    )
