"""GCS 파일 관리 엔드포인트 (Phase 6 / 6-1)

GCS 버킷의 JSONL 파일 탐색, 로컬 다운로드, GCS 업로드, 이미지 프록시 기능을 제공한다.
모든 엔드포인트는 세션 인증 필수.

HTML 뷰:
    GET /api/v1/gcs/browse            → GCS 파일 브라우저 (날짜 폴더 목록)
    GET /api/v1/gcs/browse/{date_str} → 특정 날짜 폴더 내 파일 목록

JSON API:
    GET  /api/v1/gcs/folders          → 날짜 폴더 목록 (JSON)
    GET  /api/v1/gcs/files/{date_str} → 파일 목록 (JSON)
    POST /api/v1/gcs/download         → GCS → 로컬 다운로드
    POST /api/v1/gcs/upload           → 로컬 → GCS 업로드
    GET  /api/v1/gcs/status           → GCS 연결 상태 확인

이미지 프록시:
    GET /api/v1/gcs/image/{date_str}/{image_path:path} → GCS 이미지 직접 서빙
"""

from __future__ import annotations

import asyncio
import hmac
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi_csrf_protect import CsrfProtect
from google.cloud.exceptions import GoogleCloudError, NotFound
from pydantic import BaseModel

from app.api.deps import get_current_user, get_lock_service, get_optional_user
from app.core.config import settings
from app.core.logger import logger
from app.core.rate_limit import limiter
from app.services.audit_service import audit_service
from app.services.file_service import file_service
from app.services.gcs_edit_service import gcs_edit_service
from app.services.gcs_service import gcs_service
from app.services.lock_service import LockService
from app.services.metadata_service import metadata_service

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Request / Response 스키마
# ---------------------------------------------------------------------------
class GCSDownloadRequest(BaseModel):
    gcs_path: str
    overwrite: bool = False


class GCSUploadRequest(BaseModel):
    file_id: str
    date_str: str = ""


# ---------------------------------------------------------------------------
# HTML 뷰 — GCS 파일 브라우저
# ---------------------------------------------------------------------------
async def _background_folder_sync(task: str) -> None:
    """GCS → DuckDB 폴더/파일 동기화 (백그라운드 태스크)"""
    try:
        await gcs_service.invalidate_cache()
        gcs_folders = await gcs_service.list_date_folders(task_id=task)
        for folder in gcs_folders:
            gcs_files = await gcs_service.list_files(folder["name"], task_id=task)
            if gcs_files:
                await metadata_service.sync_files_from_gcs(task, folder["name"], gcs_files)
        logger.info("Background GCS → DuckDB sync completed: task=%s folders=%d", task, len(gcs_folders))
    except Exception as e:
        logger.warning("Background GCS → DuckDB sync failed: task=%s error=%s", task, e)


@router.post("/sync")
@limiter.limit("5/minute")
async def gcs_sync(
    request: Request,
    task: str = "",
    x_sync_key: str | None = Header(None),
    current_user: dict[str, Any] | None = Depends(get_optional_user),
):
    """GCS → DuckDB 수동 동기화 API.

    registry_sync 기록을 초기화한 뒤 GCS에서 폴더/파일 목록을 다시 가져와 DuckDB에 저장한다.
    인증: 세션 쿠키 또는 X-Sync-Key 헤더 중 하나가 필요하다.
    """
    caller = "api-key"
    if x_sync_key and settings.SYNC_API_KEY:
        if not hmac.compare_digest(x_sync_key, settings.SYNC_API_KEY):
            raise HTTPException(status_code=403, detail="유효하지 않은 API 키입니다")
    elif current_user:
        caller = current_user["user_id"]
    else:
        raise HTTPException(status_code=401, detail="인증이 필요합니다 (세션 쿠키 또는 X-Sync-Key 헤더)")

    if not task or task not in settings.GCS_TASKS:
        raise HTTPException(status_code=400, detail="유효한 task 파라미터가 필요합니다")

    cleared = await metadata_service.clear_sync_record(task)
    invalidated = await gcs_service.invalidate_cache()
    logger.info(
        "Sync record cleared: task=%s rows=%d cache_keys=%d by %s",
        task, cleared, invalidated, caller,
    )

    total_folders = 0
    total_files = 0
    try:
        gcs_folders = await gcs_service.list_date_folders(task_id=task)
        for folder in gcs_folders:
            gcs_files = await gcs_service.list_files(folder["name"], task_id=task)
            if gcs_files:
                synced = await metadata_service.sync_files_from_gcs(task, folder["name"], gcs_files)
                total_files += synced
        total_folders = len(gcs_folders)
        logger.info("Manual GCS sync completed: task=%s folders=%d files=%d", task, total_folders, total_files)
    except Exception as e:
        logger.error("Manual GCS sync failed: task=%s error=%s", task, e)
        raise HTTPException(status_code=502, detail=f"GCS 동기화 실패: {e}")

    return {
        "status": "ok",
        "task": task,
        "folders_synced": total_folders,
        "files_synced": total_files,
    }


@router.get("/browse", response_class=HTMLResponse)
@limiter.limit("100/minute")
async def gcs_browse(
    request: Request,
    task: str = "",
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] | None = Depends(get_optional_user),
):
    """GCS 날짜 폴더 목록 브라우저 (TASK별) — DuckDB-first

    DuckDB에 데이터가 있으면 즉시 서빙 (지연 0).
    동기화가 필요하면 백그라운드 태스크로 처리.
    최초 접근(DB 데이터 없음)일 때만 블로킹 동기화.
    """
    if current_user is None:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=f"{settings.API_V1_STR}/view/login")

    if not task or task not in settings.GCS_TASKS:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=f"{settings.API_V1_STR}/view/files")

    task_info = settings.GCS_TASKS[task]

    folders: list[dict[str, str]] = []
    gcs_error: str = ""
    try:
        has_folders = await metadata_service.has_any_folders(task)

        if has_folders:
            folders = await metadata_service.list_date_folders(task)

            need_sync = await metadata_service.needs_folder_sync_today(task)
            if need_sync:
                asyncio.create_task(_background_folder_sync(task))
        else:
            gcs_folders = await gcs_service.list_date_folders(task_id=task)
            for folder in gcs_folders:
                gcs_files = await gcs_service.list_files(folder["name"], task_id=task)
                if gcs_files:
                    await metadata_service.sync_files_from_gcs(task, folder["name"], gcs_files)
            logger.info("Initial GCS → DuckDB sync completed: task=%s folders=%d", task, len(gcs_folders))
            folders = await metadata_service.list_date_folders(task)
    except Exception as e:
        logger.warning("DuckDB folder listing failed, falling back to GCS: %s", e)
        try:
            folders_raw = await gcs_service.list_date_folders(task_id=task)
            folders = [{"name": f["name"], "display": f["display"], "file_count": ""} for f in folders_raw]
        except Exception as e2:
            gcs_error = str(e2)

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request,
        "gcs_browse.html",
        {
            "folders": folders,
            "gcs_error": gcs_error,
            "current_user": current_user,
            "csrf_token": csrf_token,
            "bucket_name": settings.GCS_BUCKET_NAME,
            "task_id": task,
            "task_name": task_info["name"],
            "task_prefix": task_info["prefix"],
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.get("/browse/{date_str}", response_class=HTMLResponse)
@limiter.limit("100/minute")
async def gcs_browse_date(
    request: Request,
    date_str: str,
    task: str = "",
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] | None = Depends(get_optional_user),
    lock_service: LockService = Depends(get_lock_service),
):
    """특정 날짜 폴더 내 파일 목록 — DuckDB-first

    오늘 해당 (task, date_folder)가 미동기화이면 GCS에서 가져와 DuckDB에 upsert 후 서빙.
    이미 동기화된 경우 DuckDB에서 바로 서빙 (GCS 호출 없음).
    """
    if current_user is None:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=f"{settings.API_V1_STR}/view/login")

    safe_date = Path(date_str).name
    if not safe_date.isdigit() or len(safe_date) != 8:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYYMMDD)")

    task_name = ""
    if task and task in settings.GCS_TASKS:
        task_name = settings.GCS_TASKS[task]["name"]

    files: list[dict[str, Any]] = []
    gcs_error: str = ""
    try:
        if task:
            db_files = await metadata_service.list_files_by_folder(task, safe_date)
            if db_files:
                files = db_files
                need_sync = await metadata_service.needs_sync_today(task, safe_date)
                if need_sync:
                    async def _bg_file_sync() -> None:
                        try:
                            gcs_files = await gcs_service.list_files(safe_date, task_id=task)
                            if gcs_files:
                                await metadata_service.sync_files_from_gcs(task, safe_date, gcs_files)
                            logger.info("Background file sync: task=%s date=%s", task, safe_date)
                        except Exception as exc:
                            logger.warning("Background file sync failed: %s", exc)
                    asyncio.create_task(_bg_file_sync())
            else:
                gcs_files = await gcs_service.list_files(safe_date, task_id=task)
                if gcs_files:
                    await metadata_service.sync_files_from_gcs(task, safe_date, gcs_files)
                files = await metadata_service.list_files_by_folder(task, safe_date)
        else:
            files = await gcs_service.list_files(safe_date, task_id=task)
    except Exception as e:
        logger.warning("DuckDB file listing failed, falling back to GCS: %s", e)
        try:
            files = await gcs_service.list_files(safe_date, task_id=task)
        except Exception as e2:
            gcs_error = str(e2)

    local_file_names = {f["name"] for f in file_service.list_files()}

    for f in files:
        f["is_local"] = f["name"] in local_file_names

        fname = f.get("name", "")
        fid = fname[:-6] if fname.endswith(".jsonl") else fname
        lock_owner = await lock_service.check_lock(fid)
        if lock_owner:
            display = await metadata_service.get_display_name(
                lock_owner
            )
            f["lock_owner"] = display or lock_owner
            f["lock_owner_id"] = lock_owner
            if f.get("status") in ("registered", "updated", "completed"):
                f["status"] = "editing"
        else:
            f["lock_owner"] = ""
            f["lock_owner_id"] = ""
            if f.get("status") == "editing":
                f["status"] = "updated" if f.get("update_count", 0) > 0 else "registered"

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request,
        "gcs_files.html",
        {
            "files": files,
            "date_str": safe_date,
            "date_display": f"{safe_date[:4]}-{safe_date[4:6]}-{safe_date[6:8]}",
            "gcs_error": gcs_error,
            "current_user": current_user,
            "csrf_token": csrf_token,
            "bucket_name": settings.GCS_BUCKET_NAME,
            "task_id": task,
            "task_name": task_name,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


# ---------------------------------------------------------------------------
# JSON API — 폴더/파일 목록
# ---------------------------------------------------------------------------
@router.get("/folders")
@limiter.limit("60/minute")
async def api_list_folders(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """GCS 날짜 폴더 목록 (JSON)"""
    try:
        folders = await gcs_service.list_date_folders()
        return {"folders": folders}
    except GoogleCloudError as e:
        raise HTTPException(status_code=502, detail=f"GCS 오류: {e}")


@router.get("/files/{date_str}")
@limiter.limit("60/minute")
async def api_list_files(
    request: Request,
    date_str: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """특정 날짜 폴더 내 파일 목록 (JSON)"""
    safe_date = Path(date_str).name
    try:
        files = await gcs_service.list_files(safe_date)
        return {"files": files, "date": safe_date}
    except GoogleCloudError as e:
        raise HTTPException(status_code=502, detail=f"GCS 오류: {e}")


# ---------------------------------------------------------------------------
# JSON API — 다운로드 (GCS → 로컬)
# ---------------------------------------------------------------------------
@router.post("/download")
@limiter.limit("30/minute")
async def api_download(
    request: Request,
    body: GCSDownloadRequest,
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """GCS 파일을 로컬 data/ 디렉터리로 다운로드"""
    await csrf_protect.validate_csrf(request)

    try:
        local_path = await gcs_service.download_to_local(
            gcs_path=body.gcs_path,
            overwrite=body.overwrite,
        )
    except NotFound:
        raise HTTPException(status_code=404, detail="GCS 파일을 찾을 수 없습니다")
    except GoogleCloudError as e:
        raise HTTPException(status_code=502, detail=f"GCS 다운로드 실패: {e}")

    file_id = local_path.stem
    file_service._invalidate_index(file_id)

    # GCS 경로에서 date_str 추출 후 메타데이터 저장 (Phase 6-1)
    parts = body.gcs_path.strip("/").split("/")
    date_str = parts[1] if len(parts) >= 3 else ""
    if date_str:
        gcs_service.save_file_metadata(file_id, date_str, body.gcs_path)

    await audit_service.log(
        action="gcs_download",
        request=request,
        user_id=current_user["user_id"],
        display_name=current_user.get("display_name", ""),
        file_id=file_id,
        metadata={"gcs_path": body.gcs_path, "overwrite": body.overwrite},
    )

    return {
        "status": "success",
        "file_id": file_id,
        "local_path": str(local_path),
        "message": f"{local_path.name} 다운로드 완료",
    }


# ---------------------------------------------------------------------------
# 브라우저 다운로드 — 개별 JSONL 파일
# ---------------------------------------------------------------------------
@router.get("/download-file")
@limiter.limit("60/minute")
async def download_file(
    request: Request,
    gcs_path: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """GCS JSONL 파일을 브라우저로 직접 다운로드 (StreamingResponse)"""
    import io
    from urllib.parse import quote

    try:
        blob_bytes = await gcs_service.download_blob_bytes(gcs_path)
    except NotFound:
        raise HTTPException(status_code=404, detail="GCS 파일을 찾을 수 없습니다")
    except GoogleCloudError as e:
        raise HTTPException(status_code=502, detail=f"GCS 다운로드 실패: {e}")

    filename = gcs_path.split("/")[-1]
    encoded_filename = quote(filename)

    await audit_service.log(
        action="gcs_file_download",
        request=request,
        user_id=current_user["user_id"],
        display_name=current_user.get("display_name", ""),
        metadata={"gcs_path": gcs_path, "filename": filename},
    )

    return StreamingResponse(
        io.BytesIO(blob_bytes),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{encoded_filename}\"; "
                f"filename*=UTF-8''{encoded_filename}"
            ),
            "Content-Length": str(len(blob_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# 브라우저 다운로드 — JSONL 전체 ZIP (비동기 인메모리)
# ---------------------------------------------------------------------------
@router.get("/download-jsonl-info")
@limiter.limit("30/minute")
async def download_jsonl_info(
    request: Request,
    date_str: str,
    task: str = "",
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """JSONL 다운로드 사전 검증 — 파일 수, 총 용량 반환"""
    safe_date = Path(date_str).name
    if not safe_date.isdigit() or len(safe_date) != 8:
        raise HTTPException(
            status_code=400,
            detail="날짜 형식이 올바르지 않습니다 (YYYYMMDD)",
        )

    try:
        files = await gcs_service.list_all_blobs(
            safe_date, task_id=task, extensions={".jsonl"},
        )
    except Exception as e:
        logger.exception("GCS 파일 목록 조회 실패")
        raise HTTPException(status_code=502, detail=f"GCS 목록 조회 실패: {e}")

    total_size = sum(f.get("size", 0) for f in files)
    return {
        "file_count": len(files),
        "total_size": total_size,
        "total_size_display": gcs_service._human_size(total_size),
    }


@router.get("/download-jsonl-all")
@limiter.limit("10/minute")
async def download_jsonl_all(
    request: Request,
    date_str: str,
    task: str = "",
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """날짜 폴더 내 JSONL 파일을 비동기 병렬 다운로드 → 인메모리 ZIP → 응답"""
    import io
    import time
    import zipfile
    from urllib.parse import quote

    safe_date = Path(date_str).name
    if not safe_date.isdigit() or len(safe_date) != 8:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYYMMDD)")

    task_label = task or "all"
    zip_filename = f"{task_label}_{safe_date}_jsonl.zip"
    start_time = time.monotonic()

    try:
        files = await gcs_service.list_all_blobs(
            safe_date, task_id=task, extensions={".jsonl"},
        )
        if not files:
            raise HTTPException(status_code=404, detail="해당 폴더에 JSONL 파일이 없습니다")

        results = await gcs_service.download_blobs_concurrent(files)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel_path, data in results:
                zf.writestr(rel_path, data)
        zip_bytes = buf.getvalue()

        duration_ms = int((time.monotonic() - start_time) * 1000)
        total_size = sum(f.get("size", 0) for f in files)

        try:
            await metadata_service.record_download(
                user_id=current_user["user_id"],
                display_name=current_user.get("display_name", ""),
                task_id=task_label,
                date_folder=safe_date,
                file_types="jsonl",
                file_count=len(files),
                total_size=total_size,
                zip_size=len(zip_bytes),
                zip_filename=zip_filename,
                status="completed",
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.warning("Download history recording failed: %s", e)

        await audit_service.log(
            action="gcs_jsonl_download",
            request=request,
            user_id=current_user["user_id"],
            display_name=current_user.get("display_name", ""),
            metadata={
                "date_str": safe_date,
                "task": task,
                "file_count": len(files),
                "zip_filename": zip_filename,
                "zip_size": len(zip_bytes),
                "duration_ms": duration_ms,
            },
        )

        encoded_zip = quote(zip_filename)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{encoded_zip}"; '
                    f"filename*=UTF-8''{encoded_zip}"
                ),
                "Content-Length": str(len(zip_bytes)),
                "Cache-Control": "no-store",
            },
        )

    except HTTPException:
        raise
    except NotFound:
        raise HTTPException(status_code=404, detail="해당 폴더에 파일이 없습니다")
    except GoogleCloudError as e:
        logger.error("GCS 다운로드 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"GCS 다운로드 실패: {e}")
    except Exception as e:
        logger.exception("JSONL ZIP 다운로드 중 예상치 못한 오류")
        raise HTTPException(status_code=500, detail=f"다운로드 실패: {e}")


# ---------------------------------------------------------------------------
# JSON API — 업로드 (로컬 → GCS)
# ---------------------------------------------------------------------------
@router.post("/upload")
@limiter.limit("10/minute")
async def api_upload(
    request: Request,
    body: GCSUploadRequest,
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """로컬 data/ 파일을 GCS에 업로드"""
    await csrf_protect.validate_csrf(request)

    safe_id = Path(body.file_id).name
    try:
        gcs_path = await gcs_service.upload_from_local(
            file_id=safe_id,
            date_str=body.date_str,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="로컬 파일을 찾을 수 없습니다")
    except GoogleCloudError as e:
        raise HTTPException(status_code=502, detail=f"GCS 업로드 실패: {e}")

    await audit_service.log(
        action="gcs_upload",
        request=request,
        user_id=current_user["user_id"],
        display_name=current_user.get("display_name", ""),
        file_id=safe_id,
        metadata={"gcs_path": gcs_path, "date_str": body.date_str},
    )

    return {
        "status": "success",
        "gcs_path": gcs_path,
        "message": f"gs://{settings.GCS_BUCKET_NAME}/{gcs_path} 업로드 완료",
    }


# ---------------------------------------------------------------------------
# JSON API — GCS 연결 상태 확인
# ---------------------------------------------------------------------------
@router.get("/status")
async def api_gcs_status(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """GCS 연결 상태 확인"""
    result = await gcs_service.check_connection()
    return result


# ---------------------------------------------------------------------------
# GCS → Redis 로드 (편집 세션 시작)
# ---------------------------------------------------------------------------
class GCSOpenRequest(BaseModel):
    gcs_path: str
    date_str: str


@router.post("/open-edit")
@limiter.limit("30/minute")
async def open_gcs_for_edit(
    request: Request,
    body: GCSOpenRequest,
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] = Depends(get_current_user),
    lock_service: LockService = Depends(get_lock_service),
):
    """GCS JSONL을 Redis로 로드하여 편집 세션 시작 (로컬 다운로드 없음)

    기존 working copy가 Redis에 남아있으면 GCS에서 다시 로드하지 않고 재사용한다.
    (편집 종료 후 재진입 시 이전 수정사항 보존)

    편집 진입 전 Redis lock을 확인하여:
    - 다른 사용자가 활성 편집 중이면 409로 차단
    - 비활성(stale) lock이면 자동 해제 후 진행

    Returns:
        file_id, total_rows, editor_url
    """
    await csrf_protect.validate_csrf(request)

    filename = body.gcs_path.split("/")[-1]
    if not filename.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="JSONL 파일만 지원합니다.")
    file_id = filename[:-6]

    user_id = current_user["user_id"]
    lock_owner = await lock_service.check_lock(file_id)
    if lock_owner and lock_owner != user_id:
        from app.db.redis_client import get_redis_client

        redis = await get_redis_client()
        ttl = await redis.ttl(f"lock:{file_id}")
        stale_threshold = LockService.LOCK_TTL - 120
        if 0 < ttl < stale_threshold:
            await lock_service.release_lock_force(file_id)
            logger.info(
                "Stale lock auto-released: %s (owner=%s, ttl=%d)",
                file_id, lock_owner, ttl,
            )
        else:
            owner_name = await metadata_service.get_display_name(
                lock_owner
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{owner_name or lock_owner}님이 현재 편집 중입니다. "
                    "편집이 완료될 때까지 기다려주세요."
                ),
            )

    resumed = False
    if await gcs_edit_service.is_loaded(file_id):
        meta = await gcs_edit_service.get_meta(file_id)
        total_rows = int(meta["total_rows"]) if meta else 0
        resumed = True
    else:
        try:
            total_rows = await gcs_edit_service.load_from_gcs(
                file_id=file_id,
                gcs_path=body.gcs_path,
                date_str=body.date_str,
            )
        except NotFound:
            raise HTTPException(status_code=404, detail="GCS 파일을 찾을 수 없습니다.")
        except GoogleCloudError as e:
            raise HTTPException(status_code=502, detail=f"GCS 로드 실패: {e}")

    await audit_service.log(
        action="gcs_download",
        request=request,
        user_id=current_user["user_id"],
        display_name=current_user.get("display_name", ""),
        file_id=file_id,
        metadata={"gcs_path": body.gcs_path, "mode": "redis_working_copy", "resumed": resumed},
    )

    if not resumed:
        try:
            await metadata_service.on_session_start(
                gcs_path=body.gcs_path,
                user_id=current_user["user_id"],
                display_name=current_user.get("display_name", ""),
                total_rows=total_rows,
            )
        except Exception as e:
            logger.warning("DuckDB session_start record failed (non-blocking): %s", e)

    editor_url = f"{settings.API_V1_STR}/view/files/{file_id}?gcs_date={body.date_str}&mode=gcs"
    msg = f"{filename} 편집 세션 복원 ({total_rows}행)" if resumed else f"{filename} 편집 세션 시작 ({total_rows}행)"

    return {
        "status": "success",
        "file_id": file_id,
        "total_rows": total_rows,
        "editor_url": editor_url,
        "resumed": resumed,
        "message": msg,
    }


# ---------------------------------------------------------------------------
# 이미지 프록시 — GCS 이미지를 직접 서빙 (Phase 6-1)
# ---------------------------------------------------------------------------
MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


@router.get("/image/{gcs_prefix:path}")
@limiter.limit("300/minute")
async def proxy_gcs_image(
    request: Request,
    gcs_prefix: str,
):
    """GCS 이미지를 프록시하여 직접 서빙

    gcs_prefix는 GCS 내 이미지의 전체 상대 경로.
    예: manual/PROJ-14768/TASK1/20260311/images/EPT_1029/image/tag_10006_01.png
    """
    if ".." in gcs_prefix or gcs_prefix.startswith("/"):
        raise HTTPException(status_code=400, detail="잘못된 이미지 경로")

    gcs_path = gcs_prefix
    suffix = Path(gcs_path).suffix.lower()
    content_type = MIME_MAP.get(suffix, "application/octet-stream")

    def _download_blob() -> bytes:
        blob = gcs_service.bucket.blob(gcs_path)
        if not blob.exists():
            raise NotFound(f"Image not found: {gcs_path}")
        return blob.download_as_bytes()

    try:
        data = await asyncio.to_thread(_download_blob)
    except NotFound:
        raise HTTPException(status_code=404, detail=f"이미지를 찾을 수 없습니다: {gcs_prefix}")
    except GoogleCloudError as e:
        raise HTTPException(status_code=502, detail=f"GCS 이미지 로드 실패: {e}")

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-GCS-Path": gcs_path,
        },
    )


# ---------------------------------------------------------------------------
# 수정 이력 (Phase 9 — GCS Versioning Diff)
# ---------------------------------------------------------------------------
@router.get("/history", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def gcs_history(
    request: Request,
    task: str = "",
    csrf_protect: CsrfProtect = Depends(),
    current_user: dict[str, Any] | None = Depends(get_optional_user),
):
    """수정 이력 페이지 — Redis 편집 세션 기반 빠른 조회"""
    if current_user is None:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=f"{settings.API_V1_STR}/view/login")

    tasks = [
        {"id": tid, "name": info["name"]}
        for tid, info in settings.GCS_TASKS.items()
    ]

    active_sessions: list[dict[str, Any]] = []
    gcs_error = ""

    try:
        all_sessions = await gcs_edit_service.list_active_sessions()
        selected_task = task if task in settings.GCS_TASKS else ""

        for sess in all_sessions:
            gcs_path = sess.get("gcs_path", "")
            if selected_task:
                task_prefix = settings.GCS_TASKS[selected_task]["prefix"]
                if not gcs_path.startswith(task_prefix):
                    continue
            active_sessions.append({
                "file_id": sess["file_id"],
                "gcs_path": gcs_path,
                "name": gcs_path.split("/")[-1] if gcs_path else sess["file_id"],
                "total_rows": sess.get("total_rows", "0"),
                "loaded_at": sess.get("loaded_at", ""),
            })
    except Exception as e:
        gcs_error = str(e)
        selected_task = task if task in settings.GCS_TASKS else ""

    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = templates.TemplateResponse(
        request,
        "gcs_history.html",
        {
            "tasks": tasks,
            "selected_task": selected_task,
            "selected_task_name": (
                settings.GCS_TASKS[selected_task]["name"]
                if selected_task
                else ""
            ),
            "active_sessions": active_sessions,
            "gcs_error": gcs_error,
            "current_user": current_user,
            "csrf_token": csrf_token,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


@router.get("/versions/{gcs_path:path}")
@limiter.limit("60/minute")
async def get_versions(
    request: Request,
    gcs_path: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """특정 GCS 파일의 버전 목록 반환 (JSON)"""
    if not gcs_path.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="JSONL 파일만 지원합니다.")

    try:
        versions = await gcs_service.list_blob_versions(gcs_path)
    except GoogleCloudError as e:
        raise HTTPException(status_code=502, detail=f"버전 조회 실패: {e}")

    return {"gcs_path": gcs_path, "versions": versions}


@router.get("/diff")
@limiter.limit("30/minute")
async def get_diff(
    request: Request,
    gcs_path: str = "",
    gen_a: int = 0,
    gen_b: int = 0,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """두 GCS 버전의 JSONL diff 비교 (JSON)

    gen_a: 이전 버전 generation, gen_b: 이후 버전 generation
    """
    import json

    if not gcs_path or not gen_a or not gen_b:
        raise HTTPException(status_code=400, detail="gcs_path, gen_a, gen_b 필수")

    try:
        text_a = await gcs_service.download_blob_version(gcs_path, gen_a)
        text_b = await gcs_service.download_blob_version(gcs_path, gen_b)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"버전 다운로드 실패: {e}")

    def _parse_rows(text: str) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for idx, line in enumerate(text.strip().split("\n")):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                row = {"_raw": line}
            data_id = row.get("data_id", f"row_{idx}")
            rows[data_id] = row
        return rows

    def _deep_diff(
        old: Any, new: Any, prefix: str = ""
    ) -> list[dict[str, Any]]:
        """중첩 구조까지 재귀적으로 비교하여 실제 변경된 리프 경로만 반환"""
        changes: list[dict[str, Any]] = []

        if isinstance(old, dict) and isinstance(new, dict):
            all_keys = sorted(set(list(old.keys()) + list(new.keys())))
            for key in all_keys:
                if key.startswith("_"):
                    continue
                path = f"{prefix}.{key}" if prefix else key
                val_old = old.get(key)
                val_new = new.get(key)
                if isinstance(val_old, dict) and isinstance(val_new, dict):
                    changes.extend(_deep_diff(val_old, val_new, path))
                elif isinstance(val_old, list) and isinstance(val_new, list):
                    if json.dumps(val_old, sort_keys=True, ensure_ascii=False) != json.dumps(val_new, sort_keys=True, ensure_ascii=False):
                        changes.append({"path": path, "old": val_old, "new": val_new})
                else:
                    s_old = json.dumps(val_old, sort_keys=True, ensure_ascii=False)
                    s_new = json.dumps(val_new, sort_keys=True, ensure_ascii=False)
                    if s_old != s_new:
                        changes.append({"path": path, "old": val_old, "new": val_new})
        elif old != new:
            changes.append({"path": prefix or "(root)", "old": old, "new": new})

        return changes

    rows_a = _parse_rows(text_a)
    rows_b = _parse_rows(text_b)

    all_ids = list(dict.fromkeys(list(rows_a.keys()) + list(rows_b.keys())))

    diff_items: list[dict[str, Any]] = []
    for data_id in all_ids:
        in_a = data_id in rows_a
        in_b = data_id in rows_b

        if in_a and not in_b:
            diff_items.append({
                "data_id": data_id, "status": "removed",
                "old": rows_a[data_id], "new": None,
            })
        elif not in_a and in_b:
            diff_items.append({
                "data_id": data_id, "status": "added",
                "old": None, "new": rows_b[data_id],
            })
        elif in_a and in_b:
            deep_changes = _deep_diff(rows_a[data_id], rows_b[data_id])
            if deep_changes:
                changed_paths = [c["path"] for c in deep_changes]
                changed_fields = sorted(set(
                    p.split(".")[0] for p in changed_paths
                ))
                diff_items.append({
                    "data_id": data_id,
                    "status": "modified",
                    "changed_fields": changed_fields,
                    "deep_changes": deep_changes,
                    "old": rows_a[data_id],
                    "new": rows_b[data_id],
                })

    summary = {
        "total": len(all_ids),
        "added": sum(1 for d in diff_items if d["status"] == "added"),
        "removed": sum(1 for d in diff_items if d["status"] == "removed"),
        "modified": sum(1 for d in diff_items if d["status"] == "modified"),
        "unchanged": len(all_ids) - len(diff_items),
    }

    return {
        "gcs_path": gcs_path,
        "gen_a": gen_a,
        "gen_b": gen_b,
        "summary": summary,
        "diff_items": diff_items,
    }
