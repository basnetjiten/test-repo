from __future__ import annotations

import asyncio
import time
from enum import Enum, auto


class CircuitState(Enum):
    """Represents the possible states of a circuit breaker."""

    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    """Simple circuit breaker for external API calls.

    Transitions:
        CLOSED → OPEN after *failure_threshold* consecutive failures.
        OPEN → HALF_OPEN after *recovery_timeout* seconds.
        HALF_OPEN → CLOSED on success, → OPEN on failure.
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "CircuitBreaker":
        await self._check()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            await self._on_success()
        elif exc_type is asyncio.CancelledError:
            pass
        else:
            await self._on_failure()

    async def _check(self) -> None:
        async with self._lock:
            if self.state is CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Retry in {self.recovery_timeout - (time.monotonic() - self._last_failure_time):.0f}s"
                    )

    async def _on_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            if self.state is CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is in OPEN state."""


class RateLimiter:
    """Token-bucket rate limiter for async API calls.

    Limits to *max_concurrent* in-flight requests.
    Optionally smooths to *max_per_second* via a sliding window.
    """

    def __init__(
        self,
        name: str = "default",
        max_concurrent: int = 4,
        max_per_second: float = 0.0,
    ) -> None:
        self.name = name
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_per_second = max_per_second
        self._window: list[float] = []
        self._window_lock = asyncio.Lock()

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        if self._max_per_second > 0:
            await self._throttle()

    def release(self) -> None:
        self._semaphore.release()

    async def _throttle(self) -> None:
        now = time.monotonic()
        async with self._window_lock:
            self._window = [t for t in self._window if now - t < 1.0]
            if len(self._window) >= self._max_per_second:
                sleep_for = 1.0 - (now - self._window[0])
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self._window.append(time.monotonic())

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
