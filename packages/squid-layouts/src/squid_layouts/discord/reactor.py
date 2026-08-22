"""Coalesced live updates and expiry watching for Discord mounts."""

import asyncio
import logging
import weakref
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import anyio

from squid_layouts.topics import Topic, TopicBus

if TYPE_CHECKING:
    from squid_layouts.discord.mount import Mount

logger = logging.getLogger(__name__)


class Reactor:
    """Own concurrent, per-mount-coalesced refreshes and live-update expiry checks.

    Args:
        bus: Process-local topic bus used by :meth:`follow`. Without one, the reactor remains
            a standalone out-of-band refresh scheduler.
        concurrency: Maximum number of different mounts refreshed concurrently.
        sweep_interval: Seconds between interaction-token expiry checks.
        expiry_margin: How far ahead of token expiry to show paused-update chrome.
    """

    def __init__(
        self,
        bus: TopicBus | None = None,
        *,
        concurrency: int = 4,
        sweep_interval: float = 10.0,
        expiry_margin: float = 60.0,
    ) -> None:
        if concurrency < 1:
            message = "reactor concurrency must be at least one"
            raise ValueError(message)
        if sweep_interval <= 0:
            message = "reactor sweep interval must be positive"
            raise ValueError(message)
        if expiry_margin < 0:
            message = "reactor expiry margin cannot be negative"
            raise ValueError(message)
        self.bus = bus
        self.concurrency = concurrency
        self.sweep_interval = sweep_interval
        self.expiry_margin = timedelta(seconds=expiry_margin)
        self._queue: asyncio.Queue[Mount] = asyncio.Queue()
        self._queued: set[str] = set()
        self._in_flight: set[str] = set()
        self._redeliver: set[str] = set()
        self._followed: weakref.WeakKeyDictionary[Mount, int] = weakref.WeakKeyDictionary()
        self._warned_handles: weakref.WeakKeyDictionary[Mount, object] = weakref.WeakKeyDictionary()
        self._running = False

    def schedule(self, mount: Mount) -> None:
        """Enqueue a refresh while coalescing requests for the same mount."""
        if mount.finished:
            return
        if mount.id in self._in_flight:
            self._redeliver.add(mount.id)
            return
        if mount.id in self._queued:
            return
        self._queued.add(mount.id)
        self._queue.put_nowait(mount)

    def follow(self, mount: Mount, *topics: Topic) -> Callable[[], None]:
        """Refresh ``mount`` when any exact topic changes, returning an unfollow callback.

        Call this before the mount's initial send so a write cannot land between its first
        read and subscription. The mount must use this reactor as its scheduler; that lets
        several topic callbacks coalesce without running a mount concurrently with itself.
        Bindings are live-process state and must be recreated by a host recovery hook.
        """
        if self.bus is None:
            message = "cannot follow topics without a topic bus"
            raise RuntimeError(message)
        if mount.scheduler is not self:
            message = "a followed mount must use this reactor as its scheduler"
            raise ValueError(message)
        if not topics:
            message = "follow requires at least one topic"
            raise ValueError(message)

        unsubscribers: list[Callable[[], None]] = []
        active = True

        def unfollow() -> None:
            nonlocal active
            if not active:
                return
            active = False
            for unsubscribe in unsubscribers:
                unsubscribe()
            if (current := mount_ref()) is not None:
                count = self._followed.get(current, 0)
                if count <= 1:
                    self._followed.pop(current, None)
                    self._warned_handles.pop(current, None)
                else:
                    self._followed[current] = count - 1

        mount_ref = weakref.ref(mount, lambda _: unfollow())

        async def refresh(topic: Topic) -> None:
            if (current := mount_ref()) is None:
                unfollow()
                return
            await current.refresh()

        unsubscribers.extend(self.bus.subscribe(topic, refresh, label=f"mount:{mount.id}") for topic in topics)
        self._followed[mount] = self._followed.get(mount, 0) + 1

        async def finish(finished: Mount) -> None:
            unfollow()

        mount.on_finish(finish)
        return unfollow

    async def run(self) -> None:
        """Serve refreshes and expiry checks until the host cancels this coroutine."""
        if self._running:
            message = "reactor is already running"
            raise RuntimeError(message)
        self._running = True
        try:
            async with anyio.create_task_group() as tasks:
                for _ in range(self.concurrency):
                    tasks.start_soon(self._worker)
                tasks.start_soon(self._sweep)
        finally:
            self._running = False

    async def _worker(self) -> None:
        while True:
            mount = await self._queue.get()
            self._queued.discard(mount.id)
            self._in_flight.add(mount.id)
            cancelled = False
            try:
                await mount.refresh_now()
            except Exception:
                logger.exception("mount refresh failed for %s", mount.id)
            except anyio.get_cancelled_exc_class():
                cancelled = True
                raise
            finally:
                self._in_flight.discard(mount.id)
                if cancelled:
                    self._redeliver.discard(mount.id)
                elif mount.id in self._redeliver:
                    self._redeliver.discard(mount.id)
                    self.schedule(mount)
                self._queue.task_done()

    async def _sweep(self) -> None:
        while True:
            await anyio.sleep(self.sweep_interval)
            self._sweep_once(datetime.now(UTC))

    def _sweep_once(self, now: datetime) -> None:
        """Schedule the final honest refresh for handles approaching expiry."""
        for mount in tuple(self._followed):
            handle = mount.handle
            if mount.finished or handle is None or handle.permanent or handle.expires_at is None:
                self._warned_handles.pop(mount, None)
                continue
            if handle.expires_at - now > self.expiry_margin:
                self._warned_handles.pop(mount, None)
                continue
            if self._warned_handles.get(mount) is handle:
                continue
            self._warned_handles[mount] = handle
            mount.status = mount.chrome.updates_paused
            mount.invalidate()
            self.schedule(mount)
