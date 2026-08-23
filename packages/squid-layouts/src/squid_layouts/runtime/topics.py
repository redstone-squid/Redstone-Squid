"""A local, payload-free notification bus for refreshing projections.

Topic delivery is not durable. A process that exits with queued work loses it, so applications
must publish only as a latency hint over an already-correct data path. Subscribers receive the
topic address and re-read the source of truth; the bus never carries application state.
"""

import asyncio
import contextvars
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol, overload
from weakref import WeakValueDictionary

from squid_layouts.profiling import NoOpProfiler, OperationKind, Profiler, TraceLink, TraceOutcome, TraceResult

# Imported from the module rather than the `runtime` package: `runtime/__init__` pulls in
# `shared.py`, which imports this module.
from squid_layouts.runtime.reactivity import _Cell

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Topic:
    """One named address a host publishes and a render watches.

    Equal by value, so two publishers agree without sharing a constructor, and encodable in
    full, which is what makes it the only address able to cross a process boundary. Keys are
    text on purpose: `Topic("build", 123)` is a type error rather than a topic nobody else
    ever addresses.
    """

    kind: str
    key: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass(frozen=True, slots=True, eq=False)
class CellAddress:
    """A shared cell's identity, which lives and dies with this process.

    Handed out by `Observation.addresses()` and `Mount.observed`, never constructed by a
    host. It names an object rather than a value, so nothing can put it on a wire and a
    bridge does not have to decide whether to try.
    """

    owner: object
    name: str

    def __eq__(self, other: object) -> bool:
        # Identity on the namespace rather than equality: a `Shared` subclass that defines
        # `__eq__` must not be able to make two live namespaces share one address.
        if not isinstance(other, CellAddress):
            return NotImplemented
        return other.owner is self.owner and other.name == self.name

    def __hash__(self) -> int:
        return hash((id(self.owner), self.name))


type Address = Topic | CellAddress
"""Everything the bus carries: a host's named topic, or a shared cell's local identity."""

type Subscriber = Callable[[Address], Awaitable[None]]


class _TopicCell(_Cell):
    """A cell with an address and a version, and deliberately no value.

    Reading state is how this package declares a dependency, and a topic has no state to
    read -- it says only that something behind it moved. So the cell carries the version
    alone, and :func:`watch` is the read.
    """

    # `_Cell` defines `__slots__` without one, and the registry below holds cells weakly.
    __slots__ = ("__weakref__",)


_TOPIC_CELLS: WeakValueDictionary[Topic, _TopicCell] = WeakValueDictionary()
"""Every topic something is currently watching.

Weak on purpose: a cell lives exactly as long as some resource's `sources` or some live
`Observation` holds it, so a topic nobody watches costs nothing. A fresh cell at version 0
can only appear once nobody was left comparing against the old one.

Process-global rather than per-bus, matching `_EPOCH`. Two buses in one process therefore
share versions, which costs at most one redundant re-fetch -- the over-subscribe direction a
mount's follow reconciliation already takes.
"""


def _topic_cell(topic: Topic) -> _TopicCell:
    cell = _TOPIC_CELLS.get(topic)
    if cell is None:
        cell = _TopicCell(address=topic)
        _TOPIC_CELLS[topic] = cell
    return cell


def watch(*topics: Topic) -> None:
    """Depend on `topics`, so publishing one reloads this value and refreshes the mounts showing it.

    Call it inside a `sl.resource` loader, alongside the read it makes authoritative::

        @sl.resource(delivery=sl.ResourceDelivery.ATOMIC)
        async def build(self) -> Build:
            sl.watch(sl.Topic("build", self.build_id))
            return await self.queries.get(self.build_id)

    The watch is a tracked read like any other, so the resource re-pends when the topic is
    published, the render that used its value follows the topic, and a render that stops
    reading it stops following. Nothing is registered by hand and nothing has to be
    unregistered.

    A publish that lands while the loader is still awaiting is not lost: it moves the
    version the load is being compared against, so the value it produces is already stale
    and settles again.

    Not in `on_load`, which runs once per instance and under no consumer: a watch there is
    untracked and could never reload anything.
    """
    for topic in topics:
        cell = _topic_cell(topic)
        # `track(settle())`, not `read()`: `read` installs a lost-update precondition on an
        # addressed cell, which is right for a shared cell an action wrote and wrong for a
        # topic, which no action writes.
        cell.track(cell.settle())


def _invalidate(topic: Topic) -> None:
    """Move the version every watcher of `topic` is comparing against."""
    cell = _TOPIC_CELLS.get(topic)
    if cell is not None:
        cell.touch()


_SELF_PUBLISH_WARNING_THRESHOLD = 100
_NO_TOPIC = object()
_current_topic: contextvars.ContextVar[Address | object] = contextvars.ContextVar("topic_bus_topic", default=_NO_TOPIC)
_NOOP_PROFILER = NoOpProfiler()


class TopicCodec(Protocol):
    """Translate a topic to and from the text an external transport can carry.

    A host needs one of these only to speak a wire format someone else already defined;
    :class:`KindKeyCodec` is the default and is total on :class:`Topic`. `encode` returns
    `None` for a topic this codec does not claim, and the bridge keeps it local rather than
    inventing a name for it. `decode` returns `None` for text this process does not
    recognise, which is what a rolling deployment looks like from the older side.

    Only a :class:`Topic` ever reaches a codec. A :class:`CellAddress` names a live object
    rather than a value, so it cannot be encoded at all and the type says so.
    """

    def encode(self, topic: Topic) -> str | None: ...

    def decode(self, text: str) -> Topic | None: ...


class KindKeyCodec:
    """The default wire form, ``kind:key``.

    Total on :class:`Topic` unless a kind contains the separator, which would make the split
    ambiguous. A key may contain it freely, because only the first occurrence divides.
    """

    def __init__(self, separator: str = ":") -> None:
        if not separator:
            message = "topic codec separator must be a non-empty string"
            raise ValueError(message)
        self.separator = separator

    def encode(self, topic: Topic) -> str | None:
        if not topic.kind or not topic.key or self.separator in topic.kind:
            return None
        return f"{topic.kind}{self.separator}{topic.key}"

    def decode(self, text: str) -> Topic | None:
        kind, separator, key = text.partition(self.separator)
        if not separator or not kind or not key:
            return None
        return Topic(kind, key)


@dataclass(frozen=True, slots=True)
class TopicSnapshot:
    """Diagnostic state for one topic known to a bus."""

    topic: Address
    subscribers: int
    labels: tuple[str, ...]
    queued: bool
    in_flight: bool
    delivered: int
    failed: int


@dataclass(frozen=True, slots=True)
class BusSnapshot:
    """One immutable diagnostic view of a topic bus."""

    topics: tuple[TopicSnapshot, ...]
    queued: int
    in_flight: int
    delivered: int
    failed: int


@dataclass(slots=True, eq=False)
class _Subscription:
    callback: Subscriber
    label: str
    profile_label: str
    active: bool = True


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


@dataclass(slots=True)
class _TopicState:
    subscriptions: list[_Subscription] = field(default_factory=list)
    queued: bool = False
    in_flight: bool = False
    redeliver: bool = False
    delivered: int = 0
    failed: int = 0
    self_publishes: int = 0
    self_published_during_drain: bool = False
    self_publish_warned: bool = False
    queued_causes: _Causes = field(default_factory=_Causes)
    redelivery_causes: _Causes = field(default_factory=_Causes)


class TopicBus:
    """Coalesce local change notifications and deliver them with bounded concurrency.

    For every subscriber still live when :meth:`publish` returns, at least one callback for
    that topic begins after the call returns. Delivery counts and ordering between topics are
    deliberately unspecified. A topic's subscribers run sequentially in registration order,
    and at most one delivery for a topic is in flight at once.

    Addresses match by value, so two publishers that build the same :class:`Topic` agree
    without sharing a constructor. Subscriptions are process-local; bridge an external change
    feed into :meth:`publish` when multiple processes need to refresh one another. Calls must
    originate on the event-loop thread; worker threads can use
    ``loop.call_soon_threadsafe(bus.publish, topic)``.

    Args:
        concurrency: Maximum number of different topics delivered concurrently.
        profiler: Optional runtime profiler. The disabled default has negligible overhead.
        max_causal_links: Maximum distinct producer links retained for one coalesced delivery.
        clock: Monotonic clock used to measure queue latency.
    """

    def __init__(
        self,
        *,
        concurrency: int = 4,
        profiler: Profiler = _NOOP_PROFILER,
        max_causal_links: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if concurrency < 1:
            message = "topic bus concurrency must be at least one"
            raise ValueError(message)
        if max_causal_links < 0:
            message = "topic bus causal link limit cannot be negative"
            raise ValueError(message)
        self.concurrency = concurrency
        self.profiler = profiler
        self.max_causal_links = max_causal_links
        self._clock = clock
        self._queue: asyncio.Queue[Address] = asyncio.Queue()
        self._topics: dict[Address, _TopicState] = {}
        self._running = False

    @overload
    def subscribe(
        self,
        topic: Topic,
        callback: Callable[[Topic], Awaitable[None]],
        *,
        label: str = "",
        profile_label: str | None = None,
    ) -> Callable[[], None]: ...

    @overload
    def subscribe(
        self,
        topic: CellAddress,
        callback: Callable[[CellAddress], Awaitable[None]],
        *,
        label: str = "",
        profile_label: str | None = None,
    ) -> Callable[[], None]: ...

    def subscribe(
        self,
        topic: Address,
        callback: Subscriber,
        *,
        label: str = "",
        profile_label: str | None = None,
    ) -> Callable[[], None]:
        """Subscribe to an exact topic and return an idempotent unsubscribe callback.

        ``label`` remains available for instance-specific diagnostics. ``profile_label`` must
        describe a stable class of subscriber because it contributes to aggregate identities;
        it defaults to the callback's module-qualified name.
        """
        state = self._topics.setdefault(topic, _TopicState())
        stable_label = profile_label or _callback_name(callback)
        subscription = _Subscription(callback, label, stable_label)
        state.subscriptions.append(subscription)

        def unsubscribe() -> None:
            if not subscription.active:
                return
            subscription.active = False
            with suppress(ValueError):
                state.subscriptions.remove(subscription)
            self._forget_if_idle(topic, state)

        return unsubscribe

    def publish(self, *topics: Address) -> None:
        """Synchronously enqueue change notifications for all currently subscribed topics."""
        current = _current_topic.get()
        triggered = self._clock()
        link = self.profiler.capture_link()
        for topic in topics:
            if isinstance(topic, Topic):
                # Before the skip below: a mount with no reactor still has to re-fetch on its
                # next click, and that only works if the version moved.
                _invalidate(topic)
            state = self._topics.get(topic)
            if state is None or not state.subscriptions:
                continue
            if current is not _NO_TOPIC and topic == current:
                state.self_published_during_drain = True
                state.self_publishes += 1
                if state.self_publishes > _SELF_PUBLISH_WARNING_THRESHOLD and not state.self_publish_warned:
                    logger.warning("topic %r keeps publishing itself; delivery may never quiesce", topic)
                    state.self_publish_warned = True
            if state.in_flight:
                state.redelivery_causes.add(triggered, link, max_links=self.max_causal_links)
                state.redeliver = True
            else:
                state.queued_causes.add(triggered, link, max_links=self.max_causal_links)
                if not state.queued:
                    state.queued = True
                    self._queue.put_nowait(topic)

    async def run(self) -> None:
        """Serve notifications until cancelled, under the host's task supervisor.

        One call owns all of its worker tasks and cannot return while a callback is running.
        Cancellation drops deliveries already in flight; notifications not yet removed from
        the queue remain available if the bus is run again.
        """
        if self._running:
            message = "topic bus is already running"
            raise RuntimeError(message)
        self._running = True
        try:
            async with asyncio.TaskGroup() as tasks:
                for _ in range(self.concurrency):
                    tasks.create_task(self._worker())
        finally:
            self._running = False

    async def drain(self) -> None:
        """Deliver queued work, including re-entrant publishes, and return when quiet.

        This is a deterministic test seam intended for use without :meth:`run`. It cannot
        terminate if a subscriber continuously republishes its own topic.
        """
        if self._running:
            message = "cannot drain a topic bus while run() is active"
            raise RuntimeError(message)
        while not self._queue.empty():
            async with asyncio.TaskGroup() as tasks:
                for _ in range(self.concurrency):
                    try:
                        topic = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    tasks.create_task(self._deliver(topic))

    async def wait_idle(self) -> None:
        """Wait for queued topic deliveries to settle under the bus's current owner."""
        if not self._running:
            await self.drain()
            return
        await self._queue.join()

    def snapshot(self) -> BusSnapshot:
        """Return subscriber, queue, in-flight, and callback outcome diagnostics."""
        topics = tuple(
            TopicSnapshot(
                topic=topic,
                subscribers=len(state.subscriptions),
                labels=tuple(subscription.label for subscription in state.subscriptions if subscription.label),
                queued=state.queued,
                in_flight=state.in_flight,
                delivered=state.delivered,
                failed=state.failed,
            )
            for topic, state in self._topics.items()
        )
        return BusSnapshot(
            topics=topics,
            queued=sum(topic.queued for topic in topics),
            in_flight=sum(topic.in_flight for topic in topics),
            delivered=sum(topic.delivered for topic in topics),
            failed=sum(topic.failed for topic in topics),
        )

    async def _worker(self) -> None:
        while True:
            await self._deliver(await self._queue.get())

    async def _deliver(self, topic: Address) -> None:
        state = self._topics.get(topic)
        if state is None:
            self._queue.task_done()
            return
        state.queued = False
        state.in_flight = True
        causes = state.queued_causes
        state.queued_causes = _Causes()
        state.self_published_during_drain = False
        cancelled = False
        try:
            delivery_started = self._clock()
            trace_started = causes.first_triggered if causes.first_triggered is not None else delivery_started
            with self.profiler.operation(
                OperationKind.TOPIC_DELIVERY,
                name="topic",
                links=causes.links,
                started=trace_started,
            ) as operation:
                operation.record_span(
                    "queue_wait",
                    max(0.0, delivery_started - trace_started),
                    attributes={"triggers": causes.triggers, "cause_links_omitted": causes.omitted_links},
                )
                operation.increment("topic.triggers", causes.triggers)
                operation.increment("topic.coalesced", max(0, causes.triggers - 1))
                operation.increment("topic.cause_links_omitted", causes.omitted_links)
                token = _current_topic.set(topic)
                delivery_failed = False
                try:
                    for subscription in tuple(state.subscriptions):
                        if not subscription.active or subscription not in state.subscriptions:
                            continue
                        try:
                            with operation.span(f"subscriber:{subscription.profile_label}"):
                                await subscription.callback(topic)
                        except Exception:
                            delivery_failed = True
                            state.failed += 1
                            logger.exception("topic subscriber failed for %r (label=%r)", topic, subscription.label)
                        else:
                            state.delivered += 1
                finally:
                    _current_topic.reset(token)
                if delivery_failed:
                    operation.set_result(TraceResult(TraceOutcome.FAILED, detail="subscriber_failed"))
                if causes.last_triggered is not None:
                    operation.record_span("freshness", max(0.0, self._clock() - causes.last_triggered))
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            state.in_flight = False
            if cancelled:
                state.redeliver = False
                state.redelivery_causes = _Causes()
            elif state.redeliver and state.subscriptions:
                state.redeliver = False
                state.queued_causes = state.redelivery_causes
                state.redelivery_causes = _Causes()
                state.queued = True
                self._queue.put_nowait(topic)
            else:
                state.redeliver = False
                state.redelivery_causes = _Causes()
            if not state.self_published_during_drain:
                state.self_publishes = 0
                state.self_publish_warned = False
            self._forget_if_idle(topic, state)
            self._queue.task_done()

    def _forget_if_idle(self, topic: Address, state: _TopicState) -> None:
        if not state.subscriptions and not state.queued and not state.in_flight:
            self._topics.pop(topic, None)


def _callback_name(callback: Subscriber) -> str:
    module = getattr(callback, "__module__", type(callback).__module__)
    qualified = getattr(callback, "__qualname__", type(callback).__qualname__)
    return f"{module}.{qualified}"
