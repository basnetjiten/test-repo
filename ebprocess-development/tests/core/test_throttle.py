from __future__ import annotations

import asyncio

import pytest

from ebdev.core.throttle import CircuitBreaker, CircuitBreakerOpenError, RateLimiter


class TestRateLimiter:
    async def test_acquire_release(self) -> None:
        rl = RateLimiter("test", max_concurrent=2)
        async with rl:
            assert rl._semaphore._value == 1
        assert rl._semaphore._value == 2

    async def test_throttles_concurrent(self) -> None:
        rl = RateLimiter("test", max_concurrent=1)
        acquired = []

        async def grab(label: str, wait: float) -> None:
            async with rl:
                acquired.append(label)
                await asyncio.sleep(wait)

        t1 = asyncio.create_task(grab("a", 0.2))
        await asyncio.sleep(0.02)
        t2 = asyncio.create_task(grab("b", 0.01))

        # After a short delay, only t1 should have acquired
        await asyncio.sleep(0.05)
        assert acquired == ["a"], f"Expected only 'a', got {acquired}"

        await asyncio.gather(t1, t2)
        assert len(acquired) == 2, "Both should complete eventually"

    async def test_zero_concurrent(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter("test", max_concurrent=0)

    async def test_name(self) -> None:
        rl = RateLimiter("my-limiter")
        assert rl.name == "my-limiter"

    async def test_per_second_throttle(self) -> None:
        rl = RateLimiter("test", max_concurrent=10, max_per_second=2)
        times: list[float] = []

        async def grab() -> None:
            async with rl:
                times.append(asyncio.get_event_loop().time())

        tasks = [asyncio.create_task(grab()) for _ in range(4)]
        await asyncio.gather(*tasks)

        assert len(times) == 4
        # With rate 2/s and 4 requests, the total span should be > 0.5s
        span = times[-1] - times[0]
        assert span >= 0.4, f"Expected >=0.4s span from 4 reqs at 2/s, got {span:.3f}s"


class TestCircuitBreaker:
    async def test_closed_by_default(self) -> None:
        cb = CircuitBreaker("test")
        assert cb.state.name == "CLOSED"

    async def test_opens_after_failures(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)
        for _ in range(2):
            with pytest.raises(ValueError):
                async with cb:
                    raise ValueError("boom")
        assert cb.state.name == "OPEN"

    async def test_rejects_when_open(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
        with pytest.raises(ValueError):
            async with cb:
                raise ValueError("boom")

        with pytest.raises(CircuitBreakerOpenError):
            async with cb:
                pass

    async def test_half_open_recovers(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.05)

        with pytest.raises(ValueError):
            async with cb:
                raise ValueError("boom")
        assert cb.state.name == "OPEN"

        await asyncio.sleep(0.06)

        async with cb:
            pass
        assert cb.state.name == "CLOSED"

    async def test_does_not_wrap_cancelled_error(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=1)
        with pytest.raises(asyncio.CancelledError):
            async with cb:
                raise asyncio.CancelledError()
        assert cb.state.name == "CLOSED", "CancelledError should not count as failure"

    async def test_resets_on_success(self) -> None:
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60)
        with pytest.raises(ValueError):
            async with cb:
                raise ValueError("x")
        assert cb._failure_count == 1

        async with cb:
            pass
        assert cb._failure_count == 0
        assert cb.state.name == "CLOSED"

    async def test_name(self) -> None:
        cb = CircuitBreaker("my-cb")
        assert cb.name == "my-cb"
