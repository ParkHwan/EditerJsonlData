"""Health Check 엔드포인트 (Phase 4)

- Redis 연결 상태
- 데이터 디렉터리 접근
- 디스크 사용률
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.db.redis_client import redis_manager

router = APIRouter()


class HealthStatus(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    redis_ok: bool
    storage_ok: bool
    disk_usage_pct: float
    details: dict[str, str]


@router.get("/health", response_model=HealthStatus)
async def health_check() -> HealthStatus:
    """Redis, 스토리지, 디스크 사용률 확인."""
    details: dict[str, str] = {}
    redis_ok = False
    storage_ok = False
    disk_usage_pct = 0.0

    # Redis (의존성 주입 없이 직접 확인 — 미연결 시에도 200 + unhealthy 반환)
    redis = redis_manager.redis
    if redis is not None:
        try:
            await redis.ping()
            redis_ok = True
            details["redis"] = "ok"
        except Exception as e:
            details["redis"] = str(e)
    else:
        details["redis"] = "not connected"

    # 데이터 디렉터리
    data_path = Path(settings.DATA_DIR)
    if data_path.exists():
        try:
            if data_path.is_dir():
                (data_path / ".health_check").touch()
                (data_path / ".health_check").unlink(missing_ok=True)
                storage_ok = True
                details["storage"] = "ok"
            else:
                details["storage"] = "not a directory"
        except Exception as e:
            details["storage"] = str(e)
    else:
        try:
            data_path.mkdir(parents=True, exist_ok=True)
            storage_ok = True
            details["storage"] = "created"
        except Exception as e:
            details["storage"] = str(e)

    # 디스크 사용률 (DATA_DIR 기준, 없으면 루트)
    try:
        usage = shutil.disk_usage(data_path if data_path.exists() else Path("/"))
        disk_usage_pct = (usage.used / usage.total * 100) if usage.total else 0.0
        details["disk"] = f"{disk_usage_pct:.1f}%"
    except Exception as e:
        details["disk"] = str(e)

    # 종합 상태
    if not redis_ok:
        status: Literal["healthy", "degraded", "unhealthy"] = "unhealthy"
    elif not storage_ok or disk_usage_pct >= settings.DISK_USAGE_WARNING_PCT:
        status = "degraded"
    else:
        status = "healthy"

    return HealthStatus(
        status=status,
        redis_ok=redis_ok,
        storage_ok=storage_ok,
        disk_usage_pct=round(disk_usage_pct, 2),
        details=details,
    )
