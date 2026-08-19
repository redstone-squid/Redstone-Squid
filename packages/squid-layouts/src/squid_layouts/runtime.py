"""The Reactor: coalesced out-of-band re-renders.

Interaction-driven updates never come through here — they ride the interaction's own edit.
The Reactor only serves background state changes (a vote tally moving, a job finishing), and
coalesces them per mount: however many times a mount is invalidated while a render is queued
or in flight, it re-renders once with the latest state.

The host owns the task: start `Reactor.run()` under whatever supervises the process's
background work. This package never spawns tasks itself.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid_layouts.mount import Mount

logger = logging.getLogger(__name__)


class Reactor:
    """Queue-driven refresh loop with per-mount coalescing."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Mount] = asyncio.Queue()
        self._queued: set[str] = set()

    def schedule(self, mount: Mount) -> None:
        """Enqueue a mount for refresh; a mount already queued is not queued twice."""
        if mount.id in self._queued:
            return
        self._queued.add(mount.id)
        self._queue.put_nowait(mount)

    async def run(self) -> None:
        """Serve refreshes until cancelled. Run this under the host's task supervisor."""
        while True:
            mount = await self._queue.get()
            self._queued.discard(mount.id)
            try:
                await mount.refresh_now()
            except Exception:
                logger.exception("mount refresh failed")
