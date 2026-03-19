"""Graceful Shutdown — 진행 중인 저장 작업 추적 (Phase 4)"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class PendingTaskTracker:
    """진행 중인 비동기 작업 수를 추적하고, Shutdown 시 모두 완료될 때까지 대기."""

    def __init__(self) -> None:
        self._count = 0
        self._lock = asyncio.Lock()
        self._zero_event = asyncio.Event()
        self._zero_event.set()

    @property
    def count(self) -> int:
        return self._count

    @asynccontextmanager
    async def track(self) -> AsyncIterator[None]:
        """작업 시작 시 count 증가, 종료 시 감소."""
        async with self._lock:
            self._count += 1
            self._zero_event.clear()
        try:
            yield
        finally:
            async with self._lock:
                self._count -= 1
                if self._count <= 0:
                    self._count = max(0, self._count)
                    self._zero_event.set()

    async def wait_all_done(self, timeout: float = 30.0) -> None:
        """모든 추적 중인 작업이 완료될 때까지 대기 (최대 timeout초)."""
        try:
            await asyncio.wait_for(self._zero_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


pending_tracker = PendingTaskTracker()
