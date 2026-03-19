"""Draft / Auto-save API 엔드포인트 (Phase 3)

편집 중 임시 저장 (Redis) 관리.
- POST /draft/{file_id}/{row_idx}  → Draft 저장
- GET  /draft/{file_id}/{row_idx}  → Draft 조회
- DELETE /draft/{file_id}/{row_idx} → Draft 삭제
- GET  /draft/list                  → 내 Draft 목록
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi_csrf_protect import CsrfProtect
from pydantic import BaseModel

from app.api.deps import get_current_user, get_draft_service
from app.core.rate_limit import limiter
from app.services.audit_service import audit_service
from app.services.draft_service import DraftService

router = APIRouter()


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------
class DraftSaveRequest(BaseModel):
    """Draft 저장 요청"""

    content: dict[str, Any]
    version: int


class DraftResponse(BaseModel):
    """Draft 응답"""

    file_id: str
    row_idx: int
    saved_at: str
    ttl: int | None = None
    remaining_ttl: int | None = None


class DraftDataResponse(BaseModel):
    """Draft 데이터 포함 응답"""

    file_id: str
    row_idx: int
    user_id: str
    content: dict[str, Any]
    version: int
    saved_at: str
    remaining_ttl: int = 0


# ---------------------------------------------------------------------------
# Draft 저장
# ---------------------------------------------------------------------------
@router.post("/draft/{file_id}/{row_idx}", response_model=DraftResponse)
@limiter.limit("60/minute")
async def save_draft(
    request: Request,
    file_id: str,
    row_idx: int,
    body: DraftSaveRequest,
    csrf_protect: CsrfProtect = Depends(),
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """편집 중 임시 저장 (Auto-save용)"""
    await csrf_protect.validate_csrf(request)
    user_id = current_user["user_id"]

    result = await draft_service.save_draft(
        file_id=file_id,
        row_idx=row_idx,
        user_id=user_id,
        content=body.content,
        version=body.version,
    )

    return DraftResponse(**result)


# ---------------------------------------------------------------------------
# Draft 조회
# ---------------------------------------------------------------------------
@router.get("/draft/{file_id}/{row_idx}")
@limiter.limit("100/minute")
async def get_draft(
    request: Request,
    file_id: str,
    row_idx: int,
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """저장된 Draft 조회"""
    user_id = current_user["user_id"]
    draft = await draft_service.get_draft(file_id, row_idx, user_id)

    if draft is None:
        return {"exists": False, "draft": None}

    return {
        "exists": True,
        "draft": DraftDataResponse(
            file_id=draft["file_id"],
            row_idx=draft["row_idx"],
            user_id=draft["user_id"],
            content=draft["content"],
            version=draft["version"],
            saved_at=draft["saved_at"],
            remaining_ttl=draft.get("remaining_ttl", 0),
        ),
    }


# ---------------------------------------------------------------------------
# Draft 삭제
# ---------------------------------------------------------------------------
@router.delete("/draft/{file_id}/{row_idx}")
async def delete_draft(
    request: Request,
    file_id: str,
    row_idx: int,
    csrf_protect: CsrfProtect = Depends(),
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Draft 삭제 (저장 완료 또는 편집 취소 시)"""
    await csrf_protect.validate_csrf(request)
    user_id = current_user["user_id"]
    deleted = await draft_service.delete_draft(file_id, row_idx, user_id)
    return {"success": deleted, "message": "Draft 삭제 완료" if deleted else "Draft 없음"}


# ---------------------------------------------------------------------------
# 내 Draft 목록
# ---------------------------------------------------------------------------
@router.get("/draft/list")
@limiter.limit("30/minute")
async def list_my_drafts(
    request: Request,
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """현재 사용자의 모든 Draft 목록"""
    user_id = current_user["user_id"]
    drafts = await draft_service.list_user_drafts(user_id)
    return {"drafts": drafts, "count": len(drafts)}
