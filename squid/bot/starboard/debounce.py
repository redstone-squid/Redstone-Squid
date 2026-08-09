"""Coalescing refresh scheduler for starboard entries."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from squid.observability import trace_span
from squid.runtime import BackgroundTaskSupervisor

logger = logging.getLogger(__name__)
type EntryKey = tuple[int, int]
type RefreshCallback = Callable[[EntryKey, bool], Awaitable[None]]


class EntryDebouncer:
    """Coalesce bursts into one delayed refresh per starboard entry."""

    def __init__(
        self,
        callback: RefreshCallback,
        *,
        delay: float = 2.0,
        supervisor: BackgroundTaskSupervisor | None = None,
        shutdown_timeout: float = 10.0,
    ) -> None:
        self._callback = callback
        self._delay = delay
        self._supervisor = supervisor
        self._shutdown_timeout = shutdown_timeout
        self._tasks: dict[EntryKey, asyncio.Task[None]] = {}
        self._force: set[EntryKey] = set()
        self._closing = False

    def schedule(self, key: EntryKey, *, force: bool = False) -> None:
        if self._closing:
            return
        if force:
            self._force.add(key)
        if key in self._tasks:
            return
        if self._supervisor is None:
            task = asyncio.create_task(self._run(key))
        else:
            task = self._supervisor.start(
                self._run(key),
                name=f"starboard-refresh-{key[0]}-{key[1]}",
            )
        self._tasks[key] = task
        task.add_done_callback(lambda _task: self._tasks.pop(key, None))

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)

    async def close(self) -> None:
        """Cancel pending refreshes and bound extension unload latency."""
        if self._closing:
            return
        self._closing = True
        tasks = tuple(self._tasks.values())
        self._force.clear()
        for task in tasks:
            task.cancel()
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout)
        if pending:
            logger.error("Starboard refresh tasks exceeded the shutdown deadline", extra={"squid.tasks": len(pending)})

    async def _run(self, key: EntryKey) -> None:
        await asyncio.sleep(self._delay)
        force = key in self._force
        self._force.discard(key)
        try:
            with trace_span(
                "squid.background.starboard_refresh",
                {"squid.surface": "background_work"},
            ):
                await self._callback(key, force)
        except Exception:
            logger.exception("Failed to refresh starboard %s origin %s", *key)
