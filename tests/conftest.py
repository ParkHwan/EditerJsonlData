"""공유 테스트 Fixture

- fakeredis: Redis 없이 테스트 가능
- tmp_data_dir: 임시 데이터 디렉터리
- test_client: FastAPI TestClient (HTTPX)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis


# ---------------------------------------------------------------------------
# Redis Fixture (fakeredis)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def fake_redis() -> AsyncGenerator[Redis, None]:
    """fakeredis 인스턴스 (Redis 서버 불필요)"""
    server = fakeredis.aioredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# 임시 데이터 디렉터리
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_data_dir() -> Generator[Path, None, None]:
    """임시 데이터 디렉터리 (테스트 후 자동 정리)"""
    tmp = tempfile.mkdtemp(prefix="editer_test_")
    tmp_path = Path(tmp)

    # 하위 디렉터리 생성
    (tmp_path / "backups").mkdir()
    (tmp_path / "audit").mkdir()

    yield tmp_path

    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 샘플 JSONL 파일 생성
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_jsonl(tmp_data_dir: Path) -> Path:
    """3개 Row를 포함하는 샘플 JSONL 파일"""
    file_path = tmp_data_dir / "sample.jsonl"
    rows = [
        {
            "content": {"question": f"문제 {i}", "answer": f"답변 {i}"},
            "version": 1,
        }
        for i in range(1, 4)
    ]
    with open(file_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return file_path


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def app_client(
    fake_redis: Redis, tmp_data_dir: Path, sample_jsonl: Path
) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI 앱의 AsyncClient (fakeredis + 임시 디렉터리)"""
    # 설정 패치
    from app.core.config import settings

    original_data_dir = settings.DATA_DIR
    original_backup_dir = settings.BACKUP_DIR
    original_audit_dir = settings.AUDIT_DIR

    settings.DATA_DIR = str(tmp_data_dir)
    settings.BACKUP_DIR = str(tmp_data_dir / "backups")
    settings.AUDIT_DIR = str(tmp_data_dir / "audit")

    # Redis 패치
    from app.db import redis_client

    original_redis = redis_client.redis_manager.redis
    redis_client.redis_manager.redis = fake_redis

    # get_redis_client 패치
    original_get_redis = redis_client.get_redis_client

    async def patched_get_redis() -> Redis:
        return fake_redis

    redis_client.get_redis_client = patched_get_redis  # type: ignore[assignment]

    # FileService 싱글톤 패치
    from app.services.file_service import file_service

    file_service.data_dir = tmp_data_dir
    file_service.backup_dir = tmp_data_dir / "backups"
    file_service._index_cache.clear()

    # AuditService 싱글톤 패치
    from app.services.audit_service import audit_service

    audit_service.audit_dir = tmp_data_dir / "audit"

    # static 디렉터리 임시 생성 (mount 에러 방지)
    static_dir = Path("static")
    static_created = False
    if not static_dir.exists():
        static_dir.mkdir(parents=True)
        static_created = True

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # 원복
    settings.DATA_DIR = original_data_dir
    settings.BACKUP_DIR = original_backup_dir
    settings.AUDIT_DIR = original_audit_dir
    redis_client.redis_manager.redis = original_redis
    redis_client.get_redis_client = original_get_redis  # type: ignore[assignment]

    if static_created:
        shutil.rmtree(static_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 로그인 Helper
# ---------------------------------------------------------------------------
async def login_and_get_cookies(
    client: AsyncClient,
    user_id: str = "test_user",
    display_name: str = "테스트 사용자",
) -> dict[str, str]:
    """로그인 후 세션 쿠키 반환"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"user_id": user_id, "display_name": display_name},
    )
    assert resp.status_code == 200
    return dict(resp.cookies)
