"""Coalescing refresh scheduler for starboard entries."""

import logging
from collections.abc import Awaitable, Callable

import anyio

from squid.observability import TraceSurface, trace_span
from squid.runtime import BackgroundTaskSupervisor, JobHandle

logger = logging.getLogger(__name__)
type EntryKey = tuple[int, int]
type RefreshCallback = Callable[[EntryKey, bool], Awaitable[None]]


class EntryDebouncer:
    """Coalesce bursts into one refresh per entry until close cancels pending work."""

    def __init__(
        self,
        callback: RefreshCallback,
        supervisor: BackgroundTaskSupervisor,
        *,
        delay: float = 2.0,
    ) -> None:
        self._callback = callback
        self._delay = delay
        self._supervisor = supervisor
        self._handles: dict[EntryKey, JobHandle] = {}
        self._dirty: set[EntryKey] = set()
        self._force: set[EntryKey] = set()
        self._closing = False

    def schedule(self, key: EntryKey, *, force: bool = False) -> None:
        if self._closing:
            return
        if force:
            self._force.add(key)
        if key in self._handles:
            self._dirty.add(key)
            return
        self._handles[key] = self._supervisor.start(
            self._run(key),
            name=f"starboard-refresh-{key[0]}-{key[1]}",
        )

    async def drain(self) -> None:
        for handle in tuple(self._handles.values()):
            await handle.finished.wait()

    async def close(self) -> None:
        """Cancel pending refreshes and bound extension unload latency."""
        if self._closing:
            return
        self._closing = True
        self._force.clear()
        self._dirty.clear()
        await self._supervisor.cancel(*tuple(self._handles.values()))

    async def _run(self, key: EntryKey) -> None:
        try:
            while True:
                await anyio.sleep(self._delay)
                # The next read includes activity during the delay. Activity while the
                # callback awaits Discord needs another pass after that write finishes.
                self._dirty.discard(key)
                await self._refresh(key)
                if key not in self._dirty:
                    return
        finally:
            self._handles.pop(key, None)
            self._dirty.discard(key)
            self._force.discard(key)

    async def _refresh(self, key: EntryKey) -> None:
        force = key in self._force
        self._force.discard(key)
        try:
            with trace_span(
                "squid.background.starboard_refresh",
                {"squid.surface": TraceSurface.BACKGROUND_WORK},
            ):
                await self._callback(key, force)
        except Exception:
            logger.exception("Failed to refresh starboard %s origin %s", *key)
