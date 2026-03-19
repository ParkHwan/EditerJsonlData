"""Redis 연결 관리 (Sentinel / Standalone 자동 전환)

- REDIS_HOST=redis-sentinel → Sentinel 모드 (Docker Compose)
- REDIS_HOST=localhost     → Standalone 모드 (로컬 개발)
- 연결 실패 시 재시도 (최대 5회, 2초 간격)
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis, Sentinel

from app.core.config import settings
from app.core.logger import logger

MAX_RETRIES = 5
RETRY_DELAY = 2.0  # 초


class RedisManager:
    def __init__(self) -> None:
        self.redis: Redis | None = None
        self.sentinel: Sentinel | None = None

    async def connect(self) -> None:
        """Redis 연결 (Sentinel 또는 Standalone, 재시도 포함)"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if settings.REDIS_HOST == "redis-sentinel":
                    await self._connect_sentinel()
                else:
                    await self._connect_standalone()

                await self.redis.ping()  # type: ignore[union-attr]
                logger.info(
                    f"Connected to Redis at {settings.REDIS_HOST}:{settings.REDIS_PORT}"
                    f" (attempt {attempt})"
                )
                return

            except Exception as e:
                logger.warning(
                    f"Redis connection attempt {attempt}/{MAX_RETRIES} failed: {e}"
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    logger.error("All Redis connection attempts failed")
                    raise

    async def _connect_sentinel(self) -> None:
        """Sentinel 모드 연결"""
        self.sentinel = Sentinel(
            [(settings.REDIS_HOST, settings.REDIS_PORT)],
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            sentinel_kwargs={"decode_responses": True},
        )
        self.redis = self.sentinel.master_for(
            settings.REDIS_SERVICE_NAME,
            redis_class=Redis,
            decode_responses=True,
            socket_timeout=5.0,
        )
        logger.info("Using Redis Sentinel mode")

    async def _connect_standalone(self) -> None:
        """Standalone 모드 연결"""
        self.redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
        logger.info("Using Redis Standalone mode")

    async def close(self) -> None:
        if self.redis:
            await self.redis.close()


redis_manager = RedisManager()


async def get_redis_client() -> Redis:
    if not redis_manager.redis:
        await redis_manager.connect()
    return redis_manager.redis  # type: ignore[return-value]
