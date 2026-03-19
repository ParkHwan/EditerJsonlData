"""PendingTaskTracker 테스트 (Phase 5)

- 단일 작업 추적
- 동시 다수 작업 추적
- wait_all_done 타임아웃
- 카운터 정확성
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.pending_tasks import PendingTaskTracker


class TestPendingTaskTracker:
    """Graceful Shutdown 작업 추적기"""

    @pytest.mark.asyncio
    async def test_initial_state(self) -> None:
        tracker = PendingTaskTracker()
        assert tracker.count == 0

    @pytest.mark.asyncio
    async def test_single_task(self) -> None:
        tracker = PendingTaskTracker()

        async with tracker.track():
            assert tracker.count == 1

        assert tracker.count == 0

    @pytest.mark.asyncio
    async def test_concurrent_tasks(self) -> None:
        """동시 N개 작업 추적"""
        tracker = PendingTaskTracker()
        n = 10

        async def work(delay: float) -> None:
            async with tracker.track():
                await asyncio.sleep(delay)

        tasks = [asyncio.create_task(work(0.05)) for _ in range(n)]

        await asyncio.sleep(0.01)
        assert tracker.count > 0

        await asyncio.gather(*tasks)
        assert tracker.count == 0

    @pytest.mark.asyncio
    async def test_wait_all_done_immediate(self) -> None:
        """작업 없으면 즉시 반환"""
        tracker = PendingTaskTracker()
        await tracker.wait_all_done(timeout=1.0)
        assert tracker.count == 0

    @pytest.mark.asyncio
    async def test_wait_all_done_waits_for_tasks(self) -> None:
        """진행 중 작업이 완료될 때까지 대기"""
        tracker = PendingTaskTracker()

        async def slow_work() -> None:
            async with tracker.track():
                await asyncio.sleep(0.2)

        task = asyncio.create_task(slow_work())
        await asyncio.sleep(0.05)
        assert tracker.count == 1

        await tracker.wait_all_done(timeout=5.0)
        assert tracker.count == 0
        await task

    @pytest.mark.asyncio
    async def test_wait_all_done_timeout(self) -> None:
        """타임아웃 내에 완료되지 않으면 그냥 반환"""
        tracker = PendingTaskTracker()

        async def stuck_work() -> None:
            async with tracker.track():
                await asyncio.sleep(10)

        task = asyncio.create_task(stuck_work())
        await asyncio.sleep(0.05)

        await tracker.wait_all_done(timeout=0.1)
        assert tracker.count > 0

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_exception_in_task_still_decrements(self) -> None:
        """작업 중 예외 발생 시에도 카운터 감소"""
        tracker = PendingTaskTracker()

        with pytest.raises(ValueError):
            async with tracker.track():
                raise ValueError("boom")

        assert tracker.count == 0

    @pytest.mark.asyncio
    async def test_nested_tracking(self) -> None:
        """중첩 추적"""
        tracker = PendingTaskTracker()

        async with tracker.track():
            assert tracker.count == 1
            async with tracker.track():
                assert tracker.count == 2
            assert tracker.count == 1

        assert tracker.count == 0
