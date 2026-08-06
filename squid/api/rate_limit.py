"""Small process-local sliding-window abuse controls."""

import asyncio
from collections import defaultdict, deque
from collections.abc import Callable
from time import monotonic

from squid.core.errors import RateLimitedError


class SlidingWindowRateLimiter:
    """Bound writes per principal without trusting caller-supplied identifiers."""

    def __init__(self, limit: int, window_seconds: float, *, clock: Callable[[], float] = monotonic) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        """Record an event or raise when the principal exhausted its window."""
        now = self._clock()
        cutoff = now - self._window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                retry_after = max(1, int(events[0] + self._window_seconds - now))
                raise RateLimitedError(retry_after)
            events.append(now)
