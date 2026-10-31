"""Expiring in-process caching for enumerable suggestion sources.

Taxonomy and version lists are small, read on every keystroke, and change rarely — but they do
change, and the bot, API, and worker are separate processes. The existing `alru_cache` +
`cache_clear()` pattern only invalidates the process that made the edit, so a restriction alias
added over the API stays invisible to Discord autocomplete until the bot restarts. A short TTL
converges everywhere without any invalidation protocol.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

DEFAULT_TTL_SECONDS = 60.0


class TtlCache[KeyT, ValueT]:
    """Cache one loader's results per key, refreshing them after `ttl_seconds`."""

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
        """Drop one key, or everything when `key` is `None`."""
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
