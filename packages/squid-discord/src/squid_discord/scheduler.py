"""Coalesced live updates and expiry watching for Discord mounts."""

import asyncio
import logging
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, overload

import anyio

from squid_layouts.profiling import NoOpProfiler, OperationKind, PresentationStatus, Profiler, TraceLink
from squid_layouts.runtime.topics import Address, CellAddress, Topic, TopicBus

if TYPE_CHECKING:
    from squid_discord.mount import Mount

logger = logging.getLogger(__name__)
_NOOP_PROFILER = NoOpProfiler()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MountSchedulerSnapshot:
    """One immutable diagnostic view of refresh scheduling pressure."""

    queued: int
    in_flight: int
    redeliver: int
    watched: int
    scheduled: int
    coalesced: int
    delivered: int
    failed: int
    unchanged: int = 0
    """Refreshes that found the render identical to the live one and wrote nothing."""


@dataclass(slots=True)
class _Causes:
    first_triggered: float | None = None
    last_triggered: float | None = None
    triggers: int = 0
    links: list[TraceLink] = field(default_factory=list)
    omitted_links: int = 0

    def add(self, triggered: float, link: TraceLink | None, *, max_links: int) -> None:
        if self.first_triggered is None:
            self.first_triggered = triggered
        self.last_triggered = triggered
        self.triggers += 1
        if link is None or link in self.links:
            return
        if len(self.links) < max_links:
            self.links.append(link)
        else:
            self.omitted_links += 1


class MountScheduler:
    """Own concurrent, per-mount-coalesced refreshes and live-update expiry checks.

    Args:
        bus: Process-local topic bus used by :meth:`follow`. Without one, the scheduler remains
            a standalone out-of-band refresh scheduler.
        concurrency: Maximum number of different mounts refreshed concurrently.
        sweep_interval: Seconds between interaction-token expiry checks.
        clock: UTC wall clock used to compare interaction-token deadlines.
        profiler: Runtime profiler for queued refresh delivery.
        max_causal_links: Maximum distinct trigger links retained per coalesced refresh.
        monotonic: Monotonic clock used to measure queue latency.
    """

    def __init__(
        self,
        bus: TopicBus | None = None,
        *,
        concurrency: int = 4,
        sweep_interval: float = 10.0,
        clock: Callable[[], datetime] = _utc_now,
        profiler: Profiler | None = None,
        max_causal_links: int = 8,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if concurrency < 1:
            message = "scheduler concurrency must be at least one"
            raise ValueError(message)
        if sweep_interval <= 0:
            message = "scheduler sweep interval must be positive"
            raise ValueError(message)
        if max_causal_links < 0:
            message = "scheduler causal link limit cannot be negative"
            raise ValueError(message)
        self.bus = bus
        self.profiler = profiler if profiler is not None else _NOOP_PROFILER
        self.concurrency = concurrency
        self.sweep_interval = sweep_interval
        self.clock = clock
        self.max_causal_links = max_causal_links
        self._monotonic = monotonic
        self._queue: asyncio.Queue[Mount] = asyncio.Queue()
        self._queued: set[Mount] = set()
        self._in_flight: set[Mount] = set()
        self._redeliver: set[Mount] = set()
        self._queued_causes: weakref.WeakKeyDictionary[Mount, _Causes] = weakref.WeakKeyDictionary()
        self._redelivery_causes: weakref.WeakKeyDictionary[Mount, _Causes] = weakref.WeakKeyDictionary()
        self._followed: weakref.WeakKeyDictionary[Mount, int] = weakref.WeakKeyDictionary()
        self._watched: weakref.WeakSet[Mount] = weakref.WeakSet()
        self._warned_handles: weakref.WeakKeyDictionary[Mount, object] = weakref.WeakKeyDictionary()
        self._running = False
        self._scheduled = 0
        self._coalesced = 0
        self._delivered = 0
        self._failed = 0
        self._unchanged = 0

    def watch(self, mount: Mount) -> Callable[[], None]:
        """Observe a delivered mount's edit-authority deadline until it finishes."""
        if mount.finished:
            message = "cannot watch a finished mount"
            raise ValueError(message)
        if mount.scheduler is not self:
            message = "a watched mount must use this scheduler as its scheduler"
            raise ValueError(message)
        self._watched.add(mount)
        active = True
        mount_ref = weakref.ref(mount)

        def unwatch() -> None:
            nonlocal active
            if not active:
                return
            active = False
            if (current := mount_ref()) is not None:
                self._watched.discard(current)
                self._warned_handles.pop(current, None)

        return unwatch

    def schedule(self, mount: Mount) -> None:
        """Enqueue a refresh while coalescing requests for the same mount."""
        if mount.finished:
            return
        self._scheduled += 1
        triggered = self._monotonic()
        link = self.profiler.capture_link()
        if mount in self._in_flight:
            self._coalesced += 1
            self._redelivery_causes.setdefault(mount, _Causes()).add(triggered, link, max_links=self.max_causal_links)
            self._redeliver.add(mount)
            return
        if mount in self._queued:
            self._coalesced += 1
            self._queued_causes.setdefault(mount, _Causes()).add(triggered, link, max_links=self.max_causal_links)
            return
        self._queued_causes.setdefault(mount, _Causes()).add(triggered, link, max_links=self.max_causal_links)
        self._queued.add(mount)
        self._queue.put_nowait(mount)

    def snapshot(self) -> MountSchedulerSnapshot:
        """Return queue depth, coalescing, and refresh status diagnostics."""
        return MountSchedulerSnapshot(
            queued=len(self._queued),
            in_flight=len(self._in_flight),
            redeliver=len(self._redeliver),
            watched=len(self._watched),
            scheduled=self._scheduled,
            coalesced=self._coalesced,
            delivered=self._delivered,
            failed=self._failed,
            unchanged=self._unchanged,
        )

    async def wait_idle(self) -> None:
        """Wait until every refresh queued before the call has settled.

        A running scheduler owns the queue workers. Calling this while it is stopped would
        otherwise wait forever, so an operator gets an explicit lifecycle error instead.
        """
        if not self._running:
            if self._queued or self._in_flight:
                message = "cannot wait for a stopped scheduler with pending work"
                raise RuntimeError(message)
            return
        await self._queue.join()

    @overload
    def follow(self, mount: Mount, *topics: Topic) -> Callable[[], None]: ...

    @overload
    def follow(self, mount: Mount, *topics: CellAddress) -> Callable[[], None]: ...

    def follow(self, mount: Mount, *topics: Address) -> Callable[[], None]:
        """Refresh ``mount`` when any exact topic changes, returning an unfollow callback.

        Call this before the mount's initial send so a write cannot land between its first
        read and subscription. The mount must use this scheduler as its scheduler; that lets
        several topic callbacks coalesce without running a mount concurrently with itself.
        Bindings are live-process state and must be recreated by a host recovery hook.
        """
        if self.bus is None:
            message = "cannot follow topics without a topic bus"
            raise RuntimeError(message)
        if mount.finished:
            message = "cannot follow a finished mount"
            raise ValueError(message)
        if mount.scheduler is not self:
            message = "a followed mount must use this scheduler as its scheduler"
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
                else:
                    self._followed[current] = count - 1

        mount_ref = weakref.ref(mount, lambda _: unfollow())

        def refresh(topic: Address) -> None:
            if (current := mount_ref()) is None:
                unfollow()
                return
            self.schedule(current)

        unsubscribers.extend(self.bus.subscribe(topic, refresh) for topic in topics)
        self._followed[mount] = self._followed.get(mount, 0) + 1

        async def finish(finished: Mount) -> None:
            unfollow()

        mount.on_finish(finish)
        return unfollow

    async def run(self) -> None:
        """Serve refreshes and expiry checks until the host cancels this coroutine."""
        if self._running:
            message = "scheduler is already running"
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
            self._queued.discard(mount)
            self._in_flight.add(mount)
            causes = self._queued_causes.pop(mount, _Causes())
            cancelled = False
            try:
                delivery_started = self._monotonic()
                trace_started = causes.first_triggered if causes.first_triggered is not None else delivery_started
                with self.profiler.operation(
                    OperationKind.REACTOR_DELIVERY,
                    name="refresh",
                    links=causes.links,
                    started=trace_started,
                ) as operation:
                    operation.record_span(
                        "queue_wait",
                        max(0.0, delivery_started - trace_started),
                        attributes={"triggers": causes.triggers, "cause_links_omitted": causes.omitted_links},
                    )
                    operation.increment("scheduler.triggers", causes.triggers)
                    operation.increment("scheduler.coalesced", max(0, causes.triggers - 1))
                    operation.increment("scheduler.cause_links_omitted", causes.omitted_links)
                    link = self.profiler.capture_link()
                    try:
                        status = await mount.refresh(links=() if link is None else (link,))
                        if status is PresentationStatus.UNCHANGED:
                            self._unchanged += 1
                    finally:
                        if causes.last_triggered is not None:
                            operation.record_span("freshness", max(0.0, self._monotonic() - causes.last_triggered))
                    self._delivered += 1
            except Exception:
                self._failed += 1
                logger.exception("mount refresh failed for %s", mount.id)
            except anyio.get_cancelled_exc_class():
                cancelled = True
                raise
            finally:
                self._in_flight.discard(mount)
                if cancelled:
                    self._redeliver.discard(mount)
                    self._redelivery_causes.pop(mount, None)
                elif mount in self._redeliver:
                    self._redeliver.discard(mount)
                    causes = self._redelivery_causes.pop(mount, _Causes())
                    if not mount.finished:
                        self._queued_causes[mount] = causes
                        self._queued.add(mount)
                        self._queue.put_nowait(mount)
                self._queue.task_done()

    async def _sweep(self) -> None:
        while True:
            await anyio.sleep(self.sweep_interval)
            self._sweep_once()

    def _sweep_once(self) -> None:
        """Schedule the final honest refresh for handles approaching expiry."""
        now = self.clock()
        for mount in tuple(self._watched):
            handle = mount.handle
            if handle is None or not mount._should_arm_expiry(handle, now):
                self._warned_handles.pop(mount, None)
                continue
            if self._warned_handles.get(mount) is handle:
                continue
            self._warned_handles[mount] = handle
            mount._queue_expiry_arm(handle)
            self.schedule(mount)
