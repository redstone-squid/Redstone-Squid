"""Tracked topic addresses and synchronous in-process publication."""

import logging
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol, overload, runtime_checkable
from weakref import WeakValueDictionary

from squid_reactive.core import _Cell

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Topic:
    """A process-portable, payload-free address."""

    kind: str
    key: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass(frozen=True, slots=True, eq=False)
class CellAddress:
    """The process-local identity of one field on a shared reactive owner."""

    owner: object
    name: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CellAddress):
            return NotImplemented
        return other.owner is self.owner and other.name == self.name

    def __hash__(self) -> int:
        return hash((id(self.owner), self.name))


type Address = Topic | CellAddress
type Subscriber = Callable[[Address], None]
type SubscriberErrorHandler = Callable[[Address, Subscriber, Exception], None]


class _TopicCell(_Cell):
    __slots__ = ("__weakref__",)


_TOPIC_CELLS: WeakValueDictionary[Topic, _TopicCell] = WeakValueDictionary()


def _topic_cell(topic: Topic) -> _TopicCell:
    cell = _TOPIC_CELLS.get(topic)
    if cell is None:
        cell = _TopicCell(address=topic)
        _TOPIC_CELLS[topic] = cell
    return cell


def watch(*topics: Topic) -> None:
    """Record versioned reads of payload-free topics in the active read consumer."""
    for topic in topics:
        cell = _topic_cell(topic)
        cell.track(cell.version)


def _invalidate(topic: Topic) -> None:
    cell = _TOPIC_CELLS.get(topic)
    if cell is not None:
        cell.touch()


class TopicCodec(Protocol):
    """Encode the portable subset of topic addresses for an external bridge."""

    def encode(self, topic: Topic) -> str | None: ...

    def decode(self, text: str) -> Topic | None: ...


class KindKeyCodec:
    """Encode topics as ``kind<separator>key`` without escaping."""

    def __init__(self, separator: str = ":") -> None:
        if not separator:
            message = "topic separator must not be empty"
            raise ValueError(message)
        self.separator = separator

    def encode(self, topic: Topic) -> str | None:
        if self.separator in topic.kind:
            return None
        return f"{topic.kind}{self.separator}{topic.key}"

    def decode(self, text: str) -> Topic | None:
        kind, found, key = text.partition(self.separator)
        return Topic(kind, key) if found and kind else None


@runtime_checkable
class TopicBus(Protocol):
    """Small synchronous bus contract used by reactive publication and hosts.

    Implementations must advance a :class:`Topic`'s tracked version before notifying
    subscribers, even when there are no subscribers. Delivery scheduling, coalescing,
    durability, and bridges are deliberately outside this protocol.
    """

    def subscribe(self, address: Address, callback: Subscriber) -> Callable[[], None]: ...

    def publish(self, *addresses: Address) -> None: ...


@dataclass(frozen=True, slots=True)
class TopicSnapshot:
    """Compatibility diagnostics for one address on a local bus."""

    topic: Address
    subscribers: int
    queued: bool = False
    in_flight: bool = False
    delivered: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class BusSnapshot:
    """Compatibility diagnostics for synchronous local publication."""

    topics: tuple[TopicSnapshot, ...]
    queued: int = 0
    in_flight: int = 0
    delivered: int = 0
    failed: int = 0


@dataclass(slots=True)
class _Subscription:
    callback: Subscriber
    active: bool = True


@dataclass(slots=True)
class _AddressState:
    subscriptions: list[_Subscription]
    delivered: int = 0
    failed: int = 0


def _log_subscriber_error(address: Address, callback: Subscriber, error: Exception) -> None:
    logger.error(
        "topic subscriber failed for %r (%s)",
        address,
        _callback_name(callback),
        exc_info=(type(error), error, error.__traceback__),
    )


class LocalTopicBus:
    """Deliver exact-address notifications synchronously in registration order."""

    def __init__(self, *, on_subscriber_error: SubscriberErrorHandler = _log_subscriber_error) -> None:
        self._on_subscriber_error = on_subscriber_error
        self._addresses: dict[Address, _AddressState] = {}

    @overload
    def subscribe(self, address: Topic, callback: Callable[[Topic], None]) -> Callable[[], None]: ...

    @overload
    def subscribe(self, address: CellAddress, callback: Callable[[CellAddress], None]) -> Callable[[], None]: ...

    def subscribe(self, address: Address, callback: Subscriber) -> Callable[[], None]:
        state = self._addresses.setdefault(address, _AddressState([]))
        subscription = _Subscription(callback)
        state.subscriptions.append(subscription)

        def unsubscribe() -> None:
            if not subscription.active:
                return
            subscription.active = False
            with suppress(ValueError):
                state.subscriptions.remove(subscription)
            if not state.subscriptions:
                self._addresses.pop(address, None)

        return unsubscribe

    def publish(self, *addresses: Address) -> None:
        for address in addresses:
            if isinstance(address, Topic):
                _invalidate(address)
            state = self._addresses.get(address)
            if state is None:
                continue
            for subscription in tuple(state.subscriptions):
                if not subscription.active:
                    continue
                try:
                    subscription.callback(address)
                except Exception as error:
                    state.failed += 1
                    self._report(address, subscription.callback, error)
                else:
                    state.delivered += 1

    def snapshot(self) -> BusSnapshot:
        topics = tuple(
            TopicSnapshot(
                topic=address,
                subscribers=len(state.subscriptions),
                delivered=state.delivered,
                failed=state.failed,
            )
            for address, state in self._addresses.items()
        )
        return BusSnapshot(
            topics=topics,
            delivered=sum(topic.delivered for topic in topics),
            failed=sum(topic.failed for topic in topics),
        )

    def _report(self, address: Address, callback: Subscriber, error: Exception) -> None:
        try:
            self._on_subscriber_error(address, callback, error)
        except Exception:
            logger.exception("topic subscriber error hook failed while reporting %r", address)


class SubscriptionReconciler:
    """Keep subscriptions for one committed projection and at most one candidate."""

    def __init__(self, bus: TopicBus | None, callback: Subscriber) -> None:
        self.bus = bus
        self.callback = callback
        self._committed: tuple[Address, ...] = ()
        self._staged: tuple[Address, ...] | None = None
        self._follows: dict[Address, Callable[[], None]] = {}
        self._closed = False

    @property
    def committed(self) -> tuple[Address, ...]:
        return self._committed

    @property
    def staged(self) -> tuple[Address, ...] | None:
        return self._staged

    @property
    def followed(self) -> tuple[Address, ...]:
        return tuple(self._follows)

    @property
    def watched(self) -> tuple[Address, ...]:
        """Addresses read by the committed projection or its staged successor."""
        if self._staged is None:
            return self._committed
        return tuple(dict.fromkeys((*self._committed, *self._staged)))

    def stage(self, addresses: Sequence[Address]) -> None:
        if self._closed:
            message = "cannot stage subscriptions after the reconciler is closed"
            raise RuntimeError(message)
        if self._staged is not None:
            message = "a subscription candidate is already staged"
            raise RuntimeError(message)
        wanted = tuple(dict.fromkeys(addresses))
        acquired: list[Address] = []
        bus = self.bus
        try:
            for address in () if bus is None else wanted:
                if address not in self._follows:
                    self._follows[address] = bus.subscribe(address, self.callback)
                    acquired.append(address)
        except BaseException:
            for address in reversed(acquired):
                self._follows.pop(address)()
            raise
        self._staged = wanted

    def commit(self) -> None:
        staged = self._require_staged()
        self._committed = staged
        self._staged = None
        self._retire_unneeded()

    def discard(self) -> None:
        self._require_staged()
        self._staged = None
        self._retire_unneeded()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._committed = ()
        self._staged = None
        for unsubscribe in tuple(self._follows.values()):
            unsubscribe()
        self._follows.clear()

    def _require_staged(self) -> tuple[Address, ...]:
        if self._staged is None:
            message = "no subscription candidate is staged"
            raise RuntimeError(message)
        return self._staged

    def _retire_unneeded(self) -> None:
        wanted = set(self._committed)
        if self._staged is not None:
            wanted.update(self._staged)
        for address, unsubscribe in tuple(self._follows.items()):
            if address not in wanted:
                del self._follows[address]
                unsubscribe()


def _callback_name(callback: Subscriber) -> str:
    module = getattr(callback, "__module__", type(callback).__module__)
    qualified = getattr(callback, "__qualname__", type(callback).__qualname__)
    return f"{module}.{qualified}"


__all__ = [
    "Address",
    "BusSnapshot",
    "CellAddress",
    "KindKeyCodec",
    "LocalTopicBus",
    "Subscriber",
    "SubscriberErrorHandler",
    "SubscriptionReconciler",
    "Topic",
    "TopicBus",
    "TopicCodec",
    "TopicSnapshot",
    "watch",
]
