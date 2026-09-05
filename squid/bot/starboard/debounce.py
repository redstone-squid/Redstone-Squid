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
    """Coalesce bursts into one delayed refresh per starboard entry."""

    def __init__(
        self,
        callback: RefreshCallback,
        supervisor: BackgroundTaskSupervisor,
        *,
        delay: float = 2.0,
        shutdown_timeout: float = 10.0,
    ) -> None:
        self._callback = callback
        self._delay = delay
        self._supervisor = supervisor
        self._shutdown_timeout = shutdown_timeout
        self._handles: dict[EntryKey, JobHandle] = {}
        self._force: set[EntryKey] = set()
        self._closing = False

    def schedule(self, key: EntryKey, *, force: bool = False) -> None:
        if self._closing:
            return
        if force:
            self._force.add(key)
        if key in self._handles:
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
        handles = tuple(self._handles.values())
        self._force.clear()
        if not handles:
            return
        for handle in handles:
            handle.cancel()
        with anyio.move_on_after(self._shutdown_timeout):
            for handle in handles:
                await handle.finished.wait()
        pending = [handle.name for handle in handles if not handle.finished.is_set()]
        if pending:
            logger.error("Starboard refresh tasks exceeded the shutdown deadline", extra={"squid.tasks": len(pending)})

    async def _run(self, key: EntryKey) -> None:
        try:
            await anyio.sleep(self._delay)
            await self._refresh(key)
        finally:
            self._handles.pop(key, None)

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
