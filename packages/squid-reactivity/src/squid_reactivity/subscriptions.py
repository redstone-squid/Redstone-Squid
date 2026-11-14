"""Maintain one consumer's subscriptions while its dependencies change."""

from collections.abc import Callable, Sequence

from squid_reactivity.addresses import CellAddress, subscribe
from squid_reactivity.topics import Address, Subscriber, Topic, TopicBus


class Subscriptions:
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
        self._subscriptions: dict[Address, Callable[[], None]] = {}
        self._closed = False

    @property
    def committed(self) -> tuple[Address, ...]:
        return self._committed

    @property
    def staged(self) -> tuple[Address, ...] | None:
        return self._staged

    @property
    def subscribed_addresses(self) -> tuple[Address, ...]:
        """Addresses with active subscriptions, including any staged additions."""
        return tuple(self._subscriptions)

    @property
    def dependencies(self) -> tuple[Address, ...]:
        """Committed and staged dependencies, including names without a bus to subscribe to."""
        if self._staged is None:
            return self._committed
        return tuple(dict.fromkeys((*self._committed, *self._staged)))

    def stage(self, addresses: Sequence[Address]) -> None:
        if self._closed:
            message = "cannot stage subscriptions after close"
            raise RuntimeError(message)
        if self._staged is not None:
            message = "a subscription candidate is already staged"
            raise RuntimeError(message)
        wanted = tuple(dict.fromkeys(addresses))
        acquired: list[Address] = []
        bus = self.bus
        try:
            for address in wanted:
                if address in self._subscriptions:
                    continue
                if isinstance(address, CellAddress):
                    self._subscriptions[address] = subscribe(address, self.callback)
                elif isinstance(address, Topic):
                    self._subscriptions[address] = address.subscribe(self.callback)
                elif bus is not None:
                    self._subscriptions[address] = bus.subscribe(address, self.callback)
                else:
                    continue
                acquired.append(address)
        except BaseException:
            for address in reversed(acquired):
                self._subscriptions.pop(address)()
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
        """Release every committed or staged subscription and prevent reuse."""
        if self._closed:
            return
        self.clear()
        self._closed = True

    def clear(self) -> None:
        """Release subscriptions while permitting later reuse by a remounted runtime."""
        self._committed = ()
        self._staged = None
        for unsubscribe in tuple(self._subscriptions.values()):
            unsubscribe()
        self._subscriptions.clear()

    def _require_staged(self) -> tuple[Address, ...]:
        if self._staged is None:
            message = "no subscription candidate is staged"
            raise RuntimeError(message)
        return self._staged

    def _retire_unneeded(self) -> None:
        wanted = set(self._committed)
        for address, unsubscribe in tuple(self._subscriptions.items()):
            if address not in wanted:
                del self._subscriptions[address]
                unsubscribe()
