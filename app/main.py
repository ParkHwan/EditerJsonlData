"""애플리케이션 진입점 (Phase 7: 환경 분리)

- Lifespan: Redis 연결/해제, Audit 오래된 로그 정리
- CSRF Protection (fastapi-csrf-protect)
- Rate Limiting (slowapi)
- Content Security Policy + 보안 헤더 (Phase 5)
- 환경별 OpenAPI docs 노출 제어 (Phase 7)
- Static 파일 마운트
- API 라우터 통합
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi_csrf_protect.exceptions import CsrfProtectError
from slowapi.errors import RateLimitExceeded

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.security_headers import SecurityHeadersMiddleware

# CSRF 설정 로드 (import 시점에 @CsrfProtect.load_config 실행)
from app.core.csrf import get_csrf_config as _csrf_init  # noqa: F401
from app.core.exceptions import csrf_protect_exception_handler, rate_limit_exceeded_handler
from app.core.logger import logger
from app.core.pending_tasks import pending_tracker
from app.core.rate_limit import limiter
from app.db.duckdb_client import DuckDBClient
from app.db.redis_client import redis_manager
from app.services.audit_service import audit_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / Shutdown lifecycle"""
    # ── Startup ──
    logger.info(
        "Starting up Editer Jsonl Service [env=%s, debug=%s, workers=%s]",
        settings.ENVIRONMENT,
        settings.DEBUG,
        settings.effective_workers,
    )

    # Redis 연결
    try:
        await redis_manager.connect()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed (service will start in degraded mode): {e}")

    # DuckDB 초기화 (Phase 10)
    try:
        DuckDBClient.get_connection()
        logger.info("DuckDB initialized")
    except Exception as e:
        logger.warning(f"DuckDB initialization failed (service will run without metadata): {e}")

    # 최초 관리자 계정 자동 생성 (Phase 11)
    try:
        from app.services.auth_service import AuthService
        from app.services.metadata_service import metadata_service

        admin_email = "kanjanggun@crowdworks.kr"
        existing = await metadata_service.get_user_by_email(admin_email)
        if not existing:
            pw_hash = AuthService.hash_password("admin1234")
            await metadata_service.register_user(
                user_id="kanjanggun",
                display_name="박환",
                email=admin_email,
                password_hash=pw_hash,
                is_admin=True,
            )
            logger.info("Initial admin user created: %s", admin_email)
    except Exception as e:
        logger.warning("Initial admin setup failed (non-blocking): %s", e)

    # 오래된 Audit 로그 정리
    try:
        removed = await audit_service.cleanup_old_logs()
        if removed:
            logger.info(f"Cleaned up {removed} old audit log files on startup")
    except Exception as e:
        logger.warning(f"Audit log cleanup failed: {e}")

    # 고아 임시 다운로드 파일 정리 (Phase 12)
    try:
        import shutil
        from pathlib import Path
        data_dir = Path(settings.DATA_DIR)
        cleaned = 0
        for p in data_dir.glob("gcs_dl_*"):
            shutil.rmtree(p, ignore_errors=True)
            cleaned += 1
        for p in data_dir.glob("*.zip"):
            p.unlink(missing_ok=True)
            cleaned += 1
        if cleaned:
            logger.info("Cleaned up %d orphan download temp files on startup", cleaned)
    except Exception as e:
        logger.warning(f"Download temp cleanup failed: {e}")

    yield

    # ── Shutdown (Phase 4: Graceful) ──
    logger.info("Shutting down...")
    await pending_tracker.wait_all_done(timeout=30.0)
    if pending_tracker.count > 0:
        logger.warning("Shutdown timeout: %s task(s) still pending", pending_tracker.count)
    else:
        logger.info("All pending tasks completed")
    DuckDBClient.close()
    await redis_manager.close()
    logger.info("DuckDB + Redis disconnected. Shutdown complete.")


_openapi_url: str | None = f"{settings.API_V1_STR}/openapi.json" if settings.DEBUG else None

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=_openapi_url,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Rate Limiter 등록
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]

# CSRF 에러 핸들러
app.add_exception_handler(CsrfProtectError, csrf_protect_exception_handler)  # type: ignore[arg-type]

# 보안 헤더 미들웨어 (Phase 5: CSP, X-Content-Type-Options, X-Frame-Options 등)
app.add_middleware(SecurityHeadersMiddleware)

# Static 파일 마운트
app.mount("/static", StaticFiles(directory="static"), name="static")

# API 라우터
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root() -> RedirectResponse:
    """루트 → 파일 목록 페이지 리다이렉트"""
    return RedirectResponse(url=f"{settings.API_V1_STR}/view/files")
