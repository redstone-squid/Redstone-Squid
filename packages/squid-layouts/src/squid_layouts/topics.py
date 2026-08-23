"""A local, payload-free notification bus for refreshing projections.

Topic delivery is not durable. A process that exits with queued work loses it, so applications
must publish only as a latency hint over an already-correct data path. Subscribers receive the
topic address and re-read the source of truth; the bus never carries application state.
"""

import asyncio
import contextvars
import logging
import time
from collections.abc import Awaitable, Callable, Hashable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol

from squid_layouts.profiling import NoOpProfiler, OperationKind, Profiler, TraceLink, TraceOutcome, TraceResult

logger = logging.getLogger(__name__)

type Topic = Hashable
type Subscriber = Callable[[Topic], Awaitable[None]]

_SELF_PUBLISH_WARNING_THRESHOLD = 100
_NO_TOPIC = object()
_current_topic: contextvars.ContextVar[Topic | object] = contextvars.ContextVar("topic_bus_topic", default=_NO_TOPIC)
_NOOP_PROFILER = NoOpProfiler()


class TopicCodec(Protocol):
    """Translate a topic to and from the text an external transport can carry.

    A host owns its topic vocabulary, so it owns the wire form too. `encode` returns `None`
    for an address this codec cannot express -- an identity-bearing address such as a
    `Shared` cell -- and the bridge keeps that topic local rather than inventing a name for
    it. `decode` returns `None` for text this process does not recognise, which is what a
    rolling deployment looks like from the older side.
    """

    def encode(self, topic: Topic) -> str | None: ...

    def decode(self, text: str) -> Topic | None: ...


@dataclass(frozen=True, slots=True)
class TopicSnapshot:
    """Diagnostic state for one topic known to a bus."""

    topic: Topic
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

    Topics use exact hash/equality matching. Prefer a single host-side constructor for each
    tuple vocabulary: ``("build", 123)`` and ``("build", "123")`` are different topics.
    Subscriptions are process-local; bridge an external change feed into :meth:`publish` when
    multiple processes need to refresh one another. Calls must originate on the event-loop
    thread; worker threads can use ``loop.call_soon_threadsafe(bus.publish, topic)``.

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
        self._queue: asyncio.Queue[Topic] = asyncio.Queue()
        self._topics: dict[Topic, _TopicState] = {}
        self._running = False

    def subscribe(
        self,
        topic: Topic,
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

    def publish(self, *topics: Topic) -> None:
        """Synchronously enqueue change notifications for all currently subscribed topics."""
        current = _current_topic.get()
        triggered = self._clock()
        link = self.profiler.capture_link()
        for topic in topics:
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

    async def _deliver(self, topic: Topic) -> None:
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

    def _forget_if_idle(self, topic: Topic, state: _TopicState) -> None:
        if not state.subscriptions and not state.queued and not state.in_flight:
            self._topics.pop(topic, None)


def _callback_name(callback: Subscriber) -> str:
    module = getattr(callback, "__module__", type(callback).__module__)
    qualified = getattr(callback, "__qualname__", type(callback).__qualname__)
    return f"{module}.{qualified}"
