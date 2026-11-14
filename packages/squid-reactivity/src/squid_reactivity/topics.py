"""Track named dependencies and publish changes through a synchronous topic bus."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, override, runtime_checkable
from weakref import WeakValueDictionary

from squid_reactivity.addresses import CellAddress
from squid_reactivity.cells import _Cell

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TopicName:
    """A portable topic name identified by a kind and key, such as ``build:123``."""

    kind: str
    key: str

    @override
    def __str__(self) -> str:
        return f"{self.kind}:{self.key}"


class Topic:
    """A named reactive dependency owned by one bus.

    Obtain topics through bus.topic(). Looking up a topic does not track a read;
    watch() does. Each topic retains its bus.
    """

    def __init__(self, bus: TopicBus, name: TopicName) -> None:
        self._bus = bus
        self.name = name
        self._cell = _Cell(address=self)

    def watch(self) -> None:
        """Track invalidation without adding a transaction commit precondition."""
        self._cell.track(self._cell.version)

    def publish(self) -> None:
        """Invalidate this dependency and notify subscribers synchronously."""
        self._bus.publish(self.name)

    def subscribe(self, callback: Callable[[Topic], None]) -> Callable[[], None]:
        """Subscribe to this handle until the returned callable is called."""
        return self._bus.subscribe(self.name, lambda _: callback(self))

    @override
    def __str__(self) -> str:
        return str(self.name)


type Address = TopicName | Topic | CellAddress
type Subscriber = Callable[[Address], None]
type SubscriberErrorHandler = Callable[[Address, Subscriber, Exception], None]


def watch(*topics: Topic) -> None:
    """Track several bus-owned handles in the current computation or render."""
    for topic in topics:
        topic.watch()


class TopicCodec(Protocol):
    """Encode the portable subset of topic addresses for an external bridge."""

    def encode(self, topic: TopicName) -> str | None: ...

    def decode(self, text: str) -> TopicName | None: ...


class DelimitedTopicCodec:
    """Encode topics as ``kind<separator>key`` without escaping.

    The kind and key must be nonempty. The kind cannot contain the separator;
    the key can, because decoding splits only at the first occurrence. Invalid
    inputs return None. An empty separator raises ValueError at construction.
    """

    def __init__(self, separator: str = ":") -> None:
        if not separator:
            message = "topic separator must not be empty"
            raise ValueError(message)
        self.separator = separator

    def encode(self, topic: TopicName) -> str | None:
        if not topic.kind or not topic.key or self.separator in topic.kind:
            return None
        return f"{topic.kind}{self.separator}{topic.key}"

    def decode(self, text: str) -> TopicName | None:
        kind, found, key = text.partition(self.separator)
        return TopicName(kind, key) if found and kind and key else None


@runtime_checkable
class TopicBus(Protocol):
    """Publish notifications that external dependencies may have changed.

    Reactive objects notify their consumers directly, without this bus. Hosts publish
    named topics after database changes, file changes, or incoming remote events.

    Each bus owns its topic handles and advances their tracked versions before
    notifying subscribers, even when there are no subscribers. Delivery scheduling, coalescing,
    durability, and bridges are deliberately outside this protocol.
    """

    def topic(self, kind: str, key: str) -> Topic: ...

    def subscribe[AddressT: Address](
        self, address: AddressT, callback: Callable[[AddressT], None]
    ) -> Callable[[], None]: ...

    def publish(self, *addresses: Address) -> None: ...


@dataclass(frozen=True, slots=True)
class SubscriptionStats:
    """Delivery counts for one address. Queue counts are zero on a synchronous bus."""

    topic: Address
    subscribers: int
    queued: bool = False
    in_flight: bool = False
    delivered: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class TopicBusStats:
    """Delivery counts for addresses that still have subscriptions."""

    topics: tuple[SubscriptionStats, ...]
    queued: int = 0
    in_flight: int = 0
    delivered: int = 0
    failed: int = 0


@dataclass(slots=True)
class _AddressState:
    subscriptions: dict[object, Callable[[Any], None]]
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
    """Notify subscribers synchronously in registration order.

    Callbacks receive the address, not a value. An ordinary callback exception
    is reported without stopping later callbacks. Subscriptions added during
    a delivery are not called for that delivery. Those removed before their
    turn are skipped.
    """

    def __init__(self, *, on_subscriber_error: SubscriberErrorHandler = _log_subscriber_error) -> None:
        self._on_subscriber_error = on_subscriber_error
        self._addresses: dict[Address, _AddressState] = {}
        self._topics: WeakValueDictionary[TopicName, Topic] = WeakValueDictionary()

    def topic(self, kind: str, key: str) -> Topic:
        """Return this bus's handle for a portable name, without tracking a read.

        Unused handles are collected once no reader, observation, or subscription
        retains them. Publishing an unwatched name does not allocate a handle.
        """
        name = TopicName(kind, key)
        handle = self._topics.get(name)
        if handle is None:
            handle = Topic(self, name)
            self._topics[name] = handle
        return handle

    def subscribe[AddressT: Address](
        self, address: AddressT, callback: Callable[[AddressT], None]
    ) -> Callable[[], None]:
        if isinstance(address, Topic):
            return address.subscribe(lambda _: callback(address))
        state = self._addresses.setdefault(address, _AddressState({}))
        token = object()
        state.subscriptions[token] = callback

        def unsubscribe() -> None:
            if state.subscriptions.pop(token, None) is None:
                return
            if not state.subscriptions:
                self._addresses.pop(address, None)

        return unsubscribe

    def publish(self, *addresses: Address) -> None:
        for address in addresses:
            if isinstance(address, Topic):
                address.publish()
                continue
            if isinstance(address, TopicName):
                handle = self._topics.get(address)
                if handle is not None:
                    handle._cell.touch()
            state = self._addresses.get(address)
            if state is None:
                continue
            for token, callback in tuple(state.subscriptions.items()):
                if token not in state.subscriptions:
                    continue
                try:
                    callback(address)
                except Exception as error:
                    state.failed += 1
                    self._report(address, callback, error)
                else:
                    state.delivered += 1

    def snapshot(self) -> TopicBusStats:
        topics = tuple(
            SubscriptionStats(
                topic=address,
                subscribers=len(state.subscriptions),
                delivered=state.delivered,
                failed=state.failed,
            )
            for address, state in self._addresses.items()
        )
        return TopicBusStats(
            topics=topics,
            delivered=sum(topic.delivered for topic in topics),
            failed=sum(topic.failed for topic in topics),
        )

    def _report(self, address: Address, callback: Callable[[Any], None], error: Exception) -> None:
        try:
            self._on_subscriber_error(address, callback, error)
        except Exception:
            logger.exception("topic subscriber error hook failed while reporting %r", address)


def _callback_name(callback: Subscriber) -> str:
    module = getattr(callback, "__module__", type(callback).__module__)
    qualified = getattr(callback, "__qualname__", type(callback).__qualname__)
    return f"{module}.{qualified}"


__all__ = [
    "Address",
    "CellAddress",
    "DelimitedTopicCodec",
    "LocalTopicBus",
    "Subscriber",
    "SubscriberErrorHandler",
    "SubscriptionStats",
    "Topic",
    "TopicBus",
    "TopicBusStats",
    "TopicCodec",
    "TopicName",
    "watch",
]
