"""A local, payload-free notification bus for refreshing projections.

Topic delivery is not durable. A process that exits with queued work loses it, so applications
must publish only as a latency hint over an already-correct data path. Subscribers receive the
topic address and re-read the source of truth; the bus never carries application state.
"""

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable, Hashable
from contextlib import suppress
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

type Topic = Hashable
type Subscriber = Callable[[Topic], Awaitable[None]]

_SELF_PUBLISH_WARNING_THRESHOLD = 100
_NO_TOPIC = object()
_current_topic: contextvars.ContextVar[Topic | object] = contextvars.ContextVar("topic_bus_topic", default=_NO_TOPIC)


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
    active: bool = True


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
    """

    def __init__(self, *, concurrency: int = 4) -> None:
        if concurrency < 1:
            message = "topic bus concurrency must be at least one"
            raise ValueError(message)
        self.concurrency = concurrency
        self._queue: asyncio.Queue[Topic] = asyncio.Queue()
        self._topics: dict[Topic, _TopicState] = {}
        self._running = False

    def subscribe(self, topic: Topic, callback: Subscriber, *, label: str = "") -> Callable[[], None]:
        """Subscribe to an exact topic and return an idempotent unsubscribe callback."""
        state = self._topics.setdefault(topic, _TopicState())
        subscription = _Subscription(callback, label)
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
                state.redeliver = True
            elif not state.queued:
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
        state.self_published_during_drain = False
        token = _current_topic.set(topic)
        cancelled = False
        try:
            for subscription in tuple(state.subscriptions):
                if not subscription.active or subscription not in state.subscriptions:
                    continue
                try:
                    await subscription.callback(topic)
                except Exception:
                    state.failed += 1
                    logger.exception("topic subscriber failed for %r (label=%r)", topic, subscription.label)
                else:
                    state.delivered += 1
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            _current_topic.reset(token)
            state.in_flight = False
            if cancelled:
                state.redeliver = False
            elif state.redeliver and state.subscriptions:
                state.redeliver = False
                state.queued = True
                self._queue.put_nowait(topic)
            else:
                state.redeliver = False
            if not state.self_published_during_drain:
                state.self_publishes = 0
                state.self_publish_warned = False
            self._forget_if_idle(topic, state)
            self._queue.task_done()

    def _forget_if_idle(self, topic: Topic, state: _TopicState) -> None:
        if not state.subscriptions and not state.queued and not state.in_flight:
            self._topics.pop(topic, None)
