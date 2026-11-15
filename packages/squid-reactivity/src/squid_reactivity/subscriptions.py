"""Maintain one consumer's subscriptions while its dependencies change."""

from collections.abc import Callable, Sequence

from squid_reactivity.addresses import CellAddress, subscribe
from squid_reactivity.topics import DependencyAddress, Topic


class Subscriptions:
    """Update a consumer's subscriptions without missing changes during the transition.

    stage() subscribes to new addresses while retaining the previous subscriptions.
    commit() accepts the candidate set; discard() restores the previous set. Only one
    candidate may be staged at a time. close() releases everything.

    Topics carry their bus and cell addresses carry their source. A consumer that
    recomputes after a notification stages the addresses its new result read, and
    commits once that result has replaced the old one.
    """

    def __init__(self, callback: Callable[[DependencyAddress], None]) -> None:
        self.callback = callback
        self._committed: tuple[DependencyAddress, ...] = ()
        self._staged: tuple[DependencyAddress, ...] | None = None
        self._subscriptions: dict[DependencyAddress, Callable[[], None]] = {}
        self._closed = False

    @property
    def committed(self) -> tuple[DependencyAddress, ...]:
        return self._committed

    @property
    def staged(self) -> tuple[DependencyAddress, ...] | None:
        return self._staged

    @property
    def subscribed_addresses(self) -> tuple[DependencyAddress, ...]:
        """Addresses with active subscriptions, including any staged additions."""
        return tuple(self._subscriptions)

    @property
    def dependencies(self) -> tuple[DependencyAddress, ...]:
        """Committed and staged dependencies, kept subscribed until commit or discard."""
        if self._staged is None:
            return self._committed
        return tuple(dict.fromkeys((*self._committed, *self._staged)))

    def stage(self, addresses: Sequence[DependencyAddress]) -> None:
        if self._closed:
            message = "cannot stage subscriptions after close"
            raise RuntimeError(message)
        if self._staged is not None:
            message = "a subscription candidate is already staged"
            raise RuntimeError(message)
        wanted = tuple(dict.fromkeys(addresses))
        if any(not isinstance(address, (Topic, CellAddress)) for address in wanted):
            message = "subscriptions require a Topic or CellAddress; resolve topic names through a bus"
            raise TypeError(message)
        acquired: list[DependencyAddress] = []
        try:
            for address in wanted:
                if address in self._subscriptions:
                    continue
                if isinstance(address, CellAddress):
                    self._subscriptions[address] = subscribe(address, self.callback)
                else:
                    self._subscriptions[address] = address.subscribe(self.callback)
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

    def clear(self) -> None:
        """Release all subscriptions while allowing this consumer to subscribe again."""
        self._committed = ()
        self._staged = None
        for unsubscribe in tuple(self._subscriptions.values()):
            unsubscribe()
        self._subscriptions.clear()

    def close(self) -> None:
        """Release every committed or staged subscription and prevent reuse."""
        if self._closed:
            return
        self.clear()
        self._closed = True

    def _require_staged(self) -> tuple[DependencyAddress, ...]:
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
