"""In-process locks keyed by a resource ID."""

import asyncio
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager


class KeyedLock[K: Hashable]:
    """Run operations on the same key one at a time while different keys proceed in parallel.

    Each key gets its own `asyncio.Lock` while someone holds or waits for it, and the lock is
    dropped once nobody does, so the number of stored locks stays at the number of keys in use.
    This only coordinates tasks in one process.
    """

    def __init__(self) -> None:
        self._locks: dict[K, asyncio.Lock] = {}
        self._users: dict[K, int] = {}

    @asynccontextmanager
    async def hold(self, key: K) -> AsyncIterator[None]:
        """Wait for the key's lock and hold it for the duration of the block."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._users[key] -= 1
            if not self._users[key]:
                del self._users[key]
                del self._locks[key]
