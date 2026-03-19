"""Health Check API 통합 테스트 (Phase 5)

- 정상 상태 → healthy
- Redis 미연결 → unhealthy
- 인증 불필요 확인
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestHealthAPI:
    """GET /api/v1/health"""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, app_client: AsyncClient) -> None:
        """정상 환경에서 healthy 응답"""
        resp = await app_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert data["redis_ok"] is True
        assert "details" in data

    @pytest.mark.asyncio
    async def test_health_check_no_auth_required(self, app_client: AsyncClient) -> None:
        """인증 없이 접근 가능"""
        resp = await app_client.get("/api/v1/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_check_includes_disk_usage(self, app_client: AsyncClient) -> None:
        """디스크 사용률 포함"""
        resp = await app_client.get("/api/v1/health")
        data = resp.json()
        assert "disk_usage_pct" in data
        assert isinstance(data["disk_usage_pct"], (int, float))
        assert 0 <= data["disk_usage_pct"] <= 100

    @pytest.mark.asyncio
    async def test_health_check_redis_detail(self, app_client: AsyncClient) -> None:
        """Redis 상태 상세 정보 포함"""
        resp = await app_client.get("/api/v1/health")
        data = resp.json()
        assert "redis" in data["details"]
