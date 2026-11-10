"""Track named dependencies and publish changes through a synchronous topic bus."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, override, runtime_checkable
from weakref import WeakValueDictionary

from squid_reactivity.addresses import CellAddress, subscribe
from squid_reactivity.core import _Cell

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Topic:
    """A named dependency identified by a kind and key, such as ``build:123``."""

    kind: str
    key: str

    @override
    def __str__(self) -> str:
        return f"{self.kind}:{self.key}"


class TopicHandle:
    """A named reactive dependency owned by one bus.

    Obtain handles through bus.topic(). Looking up a handle does not track a read;
    watch() does. Consumers can retain a handle without separately retaining its bus.
    """

    def __init__(self, bus: TopicBus, name: Topic) -> None:
        self._bus = bus
        self.name = name
        self._cell = _Cell(address=self)

    def watch(self) -> None:
        """Track invalidation without adding a transaction commit precondition."""
        self._cell.track(self._cell.version)

    def publish(self) -> None:
        """Invalidate this dependency and notify subscribers synchronously."""
        self._bus.publish(self.name)

    def subscribe(self, callback: Callable[[TopicHandle], None]) -> Callable[[], None]:
        """Subscribe to this handle until the returned callable is called."""
        return self._bus.subscribe(self.name, lambda _: callback(self))

    @override
    def __str__(self) -> str:
        return str(self.name)


type Address = Topic | TopicHandle | CellAddress
type Subscriber = Callable[[Address], None]
type SubscriberErrorHandler = Callable[[Address, Subscriber, Exception], None]


def watch(*topics: TopicHandle) -> None:
    """Track several bus-owned handles in the current computation or render."""
    for topic in topics:
        topic.watch()


class TopicCodec(Protocol):
    """Encode the portable subset of topic addresses for an external bridge."""

    def encode(self, topic: Topic) -> str | None: ...

    def decode(self, text: str) -> Topic | None: ...


class KindKeyCodec:
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

    def encode(self, topic: Topic) -> str | None:
        if not topic.kind or not topic.key or self.separator in topic.kind:
            return None
        return f"{topic.kind}{self.separator}{topic.key}"

    def decode(self, text: str) -> Topic | None:
        kind, found, key = text.partition(self.separator)
        return Topic(kind, key) if found and kind and key else None


@runtime_checkable
class TopicBus(Protocol):
    """Publish notifications that external dependencies may have changed.

    Reactive objects notify their consumers directly, without this bus. Hosts publish
    named topics after database changes, file changes, or incoming remote events.

    Each bus owns its topic handles and advances their tracked versions before
    notifying subscribers, even when there are no subscribers. Delivery scheduling, coalescing,
    durability, and bridges are deliberately outside this protocol.
    """

    def topic(self, kind: str, key: str) -> TopicHandle: ...

    def subscribe[AddressT: Address](
        self, address: AddressT, callback: Callable[[AddressT], None]
    ) -> Callable[[], None]: ...

    def publish(self, *addresses: Address) -> None: ...


@dataclass(frozen=True, slots=True)
class TopicSnapshot:
    """Delivery counts for one address. Queue counts are zero on a synchronous bus."""

    topic: Address
    subscribers: int
    queued: bool = False
    in_flight: bool = False
    delivered: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class BusSnapshot:
    """Delivery counts for addresses that still have subscriptions."""

    topics: tuple[TopicSnapshot, ...]
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
        self._topics: WeakValueDictionary[Topic, TopicHandle] = WeakValueDictionary()

    def topic(self, kind: str, key: str) -> TopicHandle:
        """Return this bus's handle for a portable name, without tracking a read.

        Unused handles are collected once no reader, observation, or subscription
        retains them. Publishing an unwatched name does not allocate a handle.
        """
        name = Topic(kind, key)
        handle = self._topics.get(name)
        if handle is None:
            handle = TopicHandle(self, name)
            self._topics[name] = handle
        return handle

    def subscribe[AddressT: Address](
        self, address: AddressT, callback: Callable[[AddressT], None]
    ) -> Callable[[], None]:
        if isinstance(address, TopicHandle):
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
            if isinstance(address, TopicHandle):
                address.publish()
                continue
            if isinstance(address, Topic):
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

    def _report(self, address: Address, callback: Callable[[Any], None], error: Exception) -> None:
        try:
            self._on_subscriber_error(address, callback, error)
        except Exception:
            logger.exception("topic subscriber error hook failed while reporting %r", address)


class SubscriptionReconciler:
    """Keep old subscriptions active while a replacement set is prepared.

    Call stage to subscribe to new addresses without dropping the committed set.
    Then call commit to accept the new set, or discard to keep the old one.
    This overlap prevents missed notifications while a render is being delivered.
    Only one set can be staged at a time. Call close to release all subscriptions.
    """

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
            for address in wanted:
                if address in self._follows:
                    continue
                if isinstance(address, CellAddress):
                    self._follows[address] = subscribe(address.owner, address.name, self.callback)
                elif isinstance(address, TopicHandle):
                    self._follows[address] = address.subscribe(self.callback)
                elif bus is not None:
                    self._follows[address] = bus.subscribe(address, self.callback)
                else:
                    continue
                acquired.append(address)
        except BaseException:
            for address in reversed(acquired):
                self._follows.pop(address)()
            raise
        self._staged = wanted

    def commit(self) -> None:
        self._committed = self._require_staged()
        self._staged = None
        self._retire_unneeded()

    def discard(self) -> None:
        """End the staged candidate without replacing the committed projection."""
        self._require_staged()
        self._staged = None
        self._retire_unneeded()

    def close(self) -> None:
        """End reconciliation and release every committed or staged subscription."""
        if self._closed:
            return
        self.clear()
        self._closed = True

    def clear(self) -> None:
        """Release subscriptions while permitting later reuse by a remounted runtime."""
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
    "TopicHandle",
    "TopicSnapshot",
    "watch",
]
