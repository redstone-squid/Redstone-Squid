"""Cache suggestion lists within one process and refresh them on demand.

A short TTL lets processes pick up taxonomy and version changes without sharing
cache invalidation messages.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

DEFAULT_TTL_SECONDS = 60.0


class TtlCache[KeyT, ValueT]:
    """Cache successful loads, with expiry measured from when each load finishes.

    Loads for the same key share a lock; different keys can load concurrently.
    A failed refresh propagates its error instead of serving the stale value.
    Expired entries and per-key locks are not evicted; use a bounded key set.
    """

    def __init__(
        self,
        loader: Callable[[KeyT], Awaitable[ValueT]],
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._loader = loader
        self._ttl_seconds = ttl_seconds
        self._entries: dict[KeyT, tuple[float, ValueT]] = {}
        self._locks: dict[KeyT, asyncio.Lock] = {}

    async def get(self, key: KeyT) -> ValueT:
        """Return the cached value, loading or refreshing it when stale."""
        cached = self._fresh(key)
        if cached is not None:
            return cached[1]
        # One lock per key so a cold cache under concurrent keystrokes issues a single query
        # instead of one per in-flight autocomplete.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._fresh(key)
            if cached is not None:
                return cached[1]
            value = await self._loader(key)
            self._entries[key] = (time.monotonic(), value)
            return value

    def invalidate(self, key: KeyT | None = None) -> None:
        """Drop one cached value, or all values when `key` is `None`.

        In-flight loads are not cancelled and can populate the cache afterward.
        """
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    def _fresh(self, key: KeyT) -> tuple[float, ValueT] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, _ = entry
        if time.monotonic() - stored_at >= self._ttl_seconds:
            return None
        return entry
