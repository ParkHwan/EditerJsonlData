"""에디터 API 엔드포인트 (GCS Redis 편집 + 로컬 파일 편집 이중 모드)

GCS 모드 (Redis working copy):
    GCS JSONL → Redis 행별 저장 → 편집 → 최종 저장 시 GCS 덮어쓰기
    로컬 파일 I/O 없이 Redis만 사용.

로컬 모드 (기존):
    data/*.jsonl → LineIndex 기반 Random Access → Atomic Write

모드 판별: gcs_edit_service.is_loaded(file_id) → True면 GCS 모드

Lock: 파일 단위 Lock (한 사용자가 파일 편집 중이면 다른 사용자 편집 불가)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi_csrf_protect import CsrfProtect
from pydantic import BaseModel

from app.api.deps import (
    get_auth_service,
    get_current_user,
    get_draft_service,
    get_lock_service,
    get_pending_tracker,
)
from app.core.config import settings
from app.core.logger import logger
from app.core.pending_tasks import PendingTaskTracker
from app.core.rate_limit import limiter
from app.schemas.item import LockResponse
from app.services.audit_service import audit_service
from app.services.auth_service import AuthService
from app.services.draft_service import DraftService
from app.services.file_service import file_service
from app.services.gcs_edit_service import gcs_edit_service
from app.services.lock_service import LockService
from app.services.metadata_service import metadata_service
from app.services.websocket_manager import ws_manager

router = APIRouter()


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------
class SaveRequest(BaseModel):
    changes: dict[str, Any]
    version: int


# ---------------------------------------------------------------------------
# Locking Endpoints (파일 단위)
# ---------------------------------------------------------------------------
@router.post("/lock/{file_id}", response_model=LockResponse)
@limiter.limit("30/minute")
async def acquire_lock(
    request: Request,
    file_id: str,
    csrf_protect: CsrfProtect = Depends(),
    lock_service: LockService = Depends(get_lock_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """파일 단위 Lock 획득 (동일 사용자면 TTL 갱신)"""
    await csrf_protect.validate_csrf(request)
    user_id = current_user["user_id"]
    success = await lock_service.acquire_lock(file_id, user_id)
    if not success:
        current_owner = await lock_service.check_lock(file_id)
        raise HTTPException(
            status_code=409, detail=f"다른 사용자가 편집 중입니다: {current_owner}"
        )

    await audit_service.log(
        action="edit_start",
        request=request,
        user_id=user_id,
        display_name=current_user.get("display_name", ""),
        file_id=file_id,
    )

    await ws_manager.broadcast(
        file_id,
        {
            "type": "lock_change",
            "action": "acquired",
            "user_id": user_id,
            "display_name": current_user.get("display_name", ""),
        },
    )

    return LockResponse(
        success=True,
        message="File lock acquired",
        remaining_seconds=LockService.LOCK_TTL,
    )


@router.post("/lock/{file_id}/heartbeat", response_model=LockResponse)
async def lock_heartbeat(
    request: Request,
    file_id: str,
    lock_service: LockService = Depends(get_lock_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Lock TTL 연장"""
    user_id = current_user["user_id"]
    ttl = await lock_service.heartbeat(file_id, user_id)
    return LockResponse(success=True, message="Lock extended", remaining_seconds=ttl)


@router.post("/lock/{file_id}/release-beacon")
async def release_lock_beacon(
    request: Request,
    file_id: str,
    lock_service: LockService = Depends(get_lock_service),
    auth_service: AuthService = Depends(get_auth_service),
):
    """브라우저 탭/창 닫힘 시 navigator.sendBeacon으로 호출되는 Lock 해제.

    sendBeacon은 커스텀 헤더를 보낼 수 없으므로 CSRF 검증 없이
    세션 쿠키만으로 인증한다.
    """
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    user = await auth_service.validate_session(session_id, request)
    if not user:
        return {"success": False, "message": "Unauthorized"}

    user_id = user["user_id"]
    await lock_service.release_lock(file_id, user_id)

    await audit_service.log(
        action="edit_end",
        request=request,
        user_id=user_id,
        display_name=user.get("display_name", ""),
        file_id=file_id,
        metadata={"trigger": "beacon_unload"},
    )

    await ws_manager.broadcast(
        file_id,
        {"type": "lock_change", "action": "released", "user_id": user_id},
    )
    return {"success": True, "message": "Lock released via beacon"}


@router.delete("/lock/{file_id}")
async def release_lock(
    request: Request,
    file_id: str,
    csrf_protect: CsrfProtect = Depends(),
    lock_service: LockService = Depends(get_lock_service),
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """파일 Lock 해제 + 해당 파일의 모든 Draft 삭제"""
    await csrf_protect.validate_csrf(request)
    user_id = current_user["user_id"]
    await lock_service.release_lock(file_id, user_id)
    await draft_service.delete_all_drafts_for_file(file_id, user_id)

    await audit_service.log(
        action="edit_end",
        request=request,
        user_id=user_id,
        display_name=current_user.get("display_name", ""),
        file_id=file_id,
    )

    await ws_manager.broadcast(
        file_id,
        {"type": "lock_change", "action": "released", "user_id": user_id},
    )

    return {"success": True, "message": "File lock released"}


# ---------------------------------------------------------------------------
# Data Endpoints (GCS Redis / 로컬 자동 분기)
# ---------------------------------------------------------------------------
@router.get("/data/{file_id}/{row_idx}")
@limiter.limit("100/minute")
async def get_data(
    request: Request,
    file_id: str,
    row_idx: int,
):
    """Row 전체 데이터 읽기 — GCS 모드면 Redis, 아니면 로컬 파일"""
    is_gcs = await gcs_edit_service.is_loaded(file_id)

    if is_gcs:
        try:
            raw = await gcs_edit_service.get_row(file_id, row_idx)
        except KeyError:
            raise HTTPException(status_code=404, detail="Row를 찾을 수 없습니다.")
        if "version" not in raw:
            raw["version"] = raw.get("_version", 1)
        else:
            raw["version"] = raw.get("_version", raw["version"])
        return raw

    raw = await file_service.get_row_raw(file_id, row_idx)
    if "version" not in raw:
        raw["version"] = 1
    raw["row_idx"] = row_idx
    raw["file_id"] = file_id
    return raw


@router.put("/data/{file_id}/{row_idx}")
@limiter.limit("30/minute")
async def save_data(
    request: Request,
    file_id: str,
    row_idx: int,
    body: SaveRequest,
    csrf_protect: CsrfProtect = Depends(),
    lock_service: LockService = Depends(get_lock_service),
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
    pending_tracker: PendingTaskTracker = Depends(get_pending_tracker),
):
    """Row 저장 — 파일 Lock 소유자만 가능. Lock은 유지됨 (해제하지 않음)."""
    await csrf_protect.validate_csrf(request)
    user_id = current_user["user_id"]

    current_owner = await lock_service.check_lock(file_id)
    if current_owner != user_id:
        raise HTTPException(status_code=403, detail="파일 Lock을 보유하고 있지 않습니다.")

    is_gcs = await gcs_edit_service.is_loaded(file_id)

    async with pending_tracker.track():
        if is_gcs:
            try:
                updated_data = await gcs_edit_service.update_row(
                    file_id, row_idx, body.changes, body.version, user_id
                )
            except KeyError:
                raise HTTPException(status_code=404, detail="Row를 찾을 수 없습니다.")
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
        else:
            updated_data = await file_service.update_row_atomic(
                file_id, row_idx, body.changes, body.version, user_id
            )

        await draft_service.delete_draft(file_id, row_idx, user_id)

        await audit_service.log(
            action="edit_save",
            request=request,
            user_id=user_id,
            display_name=current_user.get("display_name", ""),
            file_id=file_id,
            row_idx=row_idx,
            changes=body.changes,
            metadata={"mode": "gcs_redis" if is_gcs else "local"},
        )

        if is_gcs:
            try:
                meta = await gcs_edit_service.get_meta(file_id)
                gcs_path = meta["gcs_path"] if meta else ""
                if gcs_path:
                    await metadata_service.on_row_save(
                        gcs_path=gcs_path,
                        user_id=user_id,
                        display_name=current_user.get("display_name", ""),
                        row_idx=row_idx,
                        changed_fields=list(body.changes.keys()),
                    )
            except Exception as e:
                logger.warning("DuckDB row_save record failed (non-blocking): %s", e)

        return {
            "success": True,
            "data": updated_data,
            "mode": "gcs_redis" if is_gcs else "local",
        }


# ---------------------------------------------------------------------------
# GCS 업데이트 / 편집 취소
# ---------------------------------------------------------------------------
@router.post("/publish/{file_id}")
@limiter.limit("10/minute")
async def publish_to_gcs(
    request: Request,
    file_id: str,
    csrf_protect: CsrfProtect = Depends(),
    lock_service: LockService = Depends(get_lock_service),
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Redis working copy를 GCS에 최종 업데이트 (덮어쓰기) + 파일 Lock 해제"""
    await csrf_protect.validate_csrf(request)
    user_id = current_user["user_id"]

    if not await gcs_edit_service.is_loaded(file_id):
        raise HTTPException(status_code=404, detail="GCS 편집 세션을 찾을 수 없습니다.")

    try:
        gcs_path = await gcs_edit_service.publish_to_gcs(file_id)
    except Exception as e:
        logger.error(f"GCS update failed: {file_id}: {e}")
        raise HTTPException(status_code=502, detail=f"GCS 업데이트 실패: {e}")

    await lock_service.release_lock(file_id, user_id)
    await draft_service.delete_all_drafts_for_file(file_id, user_id)

    await audit_service.log(
        action="gcs_upload",
        request=request,
        user_id=user_id,
        display_name=current_user.get("display_name", ""),
        file_id=file_id,
        metadata={"gcs_path": gcs_path, "action": "publish"},
    )

    try:
        meta = await gcs_edit_service.get_meta(file_id)
        total_rows = int(meta.get("total_rows", 0)) if meta else 0
        await metadata_service.on_gcs_update(
            gcs_path=gcs_path,
            user_id=user_id,
            display_name=current_user.get("display_name", ""),
            total_rows=total_rows,
        )
    except Exception as e:
        logger.warning("DuckDB gcs_update record failed (non-blocking): %s", e)

    await ws_manager.broadcast(
        file_id,
        {"type": "lock_change", "action": "released", "user_id": user_id},
    )

    return {
        "success": True,
        "gcs_path": gcs_path,
        "message": f"GCS 파일 업데이트 완료: {gcs_path}",
    }


@router.post("/discard/{file_id}")
@limiter.limit("30/minute")
async def discard_working_copy(
    request: Request,
    file_id: str,
    csrf_protect: CsrfProtect = Depends(),
    lock_service: LockService = Depends(get_lock_service),
    draft_service: DraftService = Depends(get_draft_service),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Redis working copy 삭제 (편집 취소, 변경사항 폐기) + 파일 Lock 해제"""
    await csrf_protect.validate_csrf(request)
    user_id = current_user["user_id"]

    if not await gcs_edit_service.is_loaded(file_id):
        raise HTTPException(status_code=404, detail="GCS 편집 세션을 찾을 수 없습니다.")

    meta = await gcs_edit_service.get_meta(file_id)
    gcs_path = meta["gcs_path"] if meta else ""

    await gcs_edit_service.discard(file_id)
    await lock_service.release_lock(file_id, user_id)
    await draft_service.delete_all_drafts_for_file(file_id, user_id)

    await audit_service.log(
        action="edit_cancel",
        request=request,
        user_id=user_id,
        display_name=current_user.get("display_name", ""),
        file_id=file_id,
        metadata={"action": "discard_working_copy"},
    )

    if gcs_path:
        try:
            await metadata_service.on_session_discard(
                gcs_path=gcs_path,
                user_id=user_id,
                display_name=current_user.get("display_name", ""),
            )
        except Exception as e:
            logger.warning("DuckDB session_discard record failed (non-blocking): %s", e)

    await ws_manager.broadcast(
        file_id,
        {"type": "lock_change", "action": "released", "user_id": user_id},
    )

    return {"success": True, "message": "편집 세션이 취소되었습니다."}


# ---------------------------------------------------------------------------
# data_id 목록 + 단일 카드 렌더링
# ---------------------------------------------------------------------------
@router.get("/ids/{file_id}")
@limiter.limit("100/minute")
async def get_data_id_list(
    request: Request,
    file_id: str,
):
    """파일 내 모든 Row의 data_id 목록 반환"""
    is_gcs = await gcs_edit_service.is_loaded(file_id)

    if is_gcs:
        ids = await gcs_edit_service.get_data_id_list(file_id)
    else:
        ids = await file_service.get_data_id_list(file_id)

    return {"file_id": file_id, "total": len(ids), "items": ids}


@router.get("/card/{file_id}/{row_idx}")
@limiter.limit("100/minute")
async def get_rendered_card(
    request: Request,
    file_id: str,
    row_idx: int,
    gcs_date: str = "",
):
    """단일 Row를 렌더링된 HTML 카드로 반환"""
    from pathlib import Path

    from fastapi.responses import HTMLResponse

    from app.core.config import settings
    from app.services.gcs_service import gcs_service
    from app.services.render_service import get_images_from_folder, render_item_card

    is_gcs = await gcs_edit_service.is_loaded(file_id)
    gcs_folder_prefix: str = ""

    if is_gcs:
        try:
            row_data = await gcs_edit_service.get_row(file_id, row_idx)
        except KeyError:
            raise HTTPException(status_code=404, detail="Row를 찾을 수 없습니다.")
        meta = await gcs_edit_service.get_meta(file_id)
        if meta and meta.get("gcs_path"):
            gcs_folder_prefix = meta["gcs_path"].rsplit("/", 1)[0]
    else:
        row_data = await file_service.get_row_raw(file_id, row_idx)
        date_str = gcs_date.strip()
        if not date_str:
            date_str = gcs_service.get_date_str_for_file(file_id) or ""
        if date_str:
            gcs_folder_prefix = f"{settings.GCS_PREFIX.rstrip('/')}/{date_str}"

    gcs_image_base_url: str | None = None
    if gcs_folder_prefix:
        gcs_image_base_url = f"{settings.API_V1_STR}/gcs/image/{gcs_folder_prefix}"

    comparison: dict[str, str] = {}
    if not gcs_image_base_url:
        images_folder = Path(settings.DATA_DIR) / "images"
        comparison = get_images_from_folder(images_folder, f"{file_id}.jsonl")

    card_html = render_item_card(
        row_idx + 1, row_data, comparison, gcs_image_base_url
    )
    return HTMLResponse(content=card_html)
