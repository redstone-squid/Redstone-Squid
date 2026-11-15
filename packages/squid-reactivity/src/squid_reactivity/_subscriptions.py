"""Listen for changes to the reactive state a consumer read.

on_change() is the ready-made consumer: it calls a function when a value computed
from reactive state changes. Subscriptions is the part underneath, for a consumer
that decides for itself when to recompute.
"""

import logging
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Self

from squid_reactivity._addresses import CellAddress, subscribe
from squid_reactivity._core import _equal
from squid_reactivity._tracking import Dependencies, observe_reads, untracked
from squid_reactivity.topics import DependencyAddress, Topic
from squid_reactivity.transactions._context import continuation_callback
from squid_reactivity.transactions._state import _CURRENT_TRANSACTION

_log = logging.getLogger(__name__)


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


class ChangeListener[ValueT]:
    """A running on_change() registration. Close it to stop the calls.

    Models hold their listeners weakly, so a listener that nothing refers to stops
    without being closed. Keep it for as long as you want the calls.
    """

    def __init__(self, read: Callable[[], ValueT], callback: Callable[[ValueT], None]) -> None:
        self._read = read
        self._callback = callback
        self._closed = False
        self._subscriptions = Subscriptions(self._notified)
        with _CURRENT_TRANSACTION.set(None), observe_reads() as dependencies:
            self._value = read()
        self._dependencies: Dependencies = dependencies
        self._subscribe()

    def close(self) -> None:
        """Stop listening. Closing twice is harmless."""
        self._closed = True
        self._subscriptions.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    def _subscribe(self) -> None:
        self._subscriptions.stage(self._dependencies.addresses())
        self._subscriptions.commit()

    def _notified(self, address: DependencyAddress) -> None:
        if self._closed:
            return
        # Read committed state even when the notification arrives inside an open transaction,
        # as one from a signal or a topic can: its staged writes may still be discarded.
        with _CURRENT_TRANSACTION.set(None):
            # A commit notifies once per written field, after all of its values are published.
            # The first notification recomputes; the rest find nothing newer and stop here.
            if self._dependencies.current():
                return
            try:
                with observe_reads() as dependencies:
                    value = self._read()
            except Exception:
                # The old dependencies stay subscribed and out of date, so the next change retries.
                _log.exception("an on_change read function failed")
                return
            self._dependencies = dependencies
            self._subscribe()
            if _equal(self._value, value):
                return
            self._value = value
            with untracked(), continuation_callback():
                try:
                    self._callback(value)
                except Exception:
                    _log.exception("an on_change callback failed")


def on_change[ValueT](read: Callable[[], ValueT], callback: Callable[[ValueT], None]) -> ChangeListener[ValueT]:
    """Call `callback` with the new value whenever the result of `read` changes.

    `read` runs once now, to learn what it depends on, and again after a change to
    anything it read. It is read-only, like a computed function. `callback` runs only
    when the new result differs from the last one, so a write that leaves the result
    equal is not reported. It is not called for the initial value.

    One transaction produces at most one call, made after its writes are published.
    A transaction that rolls back produces none. Both functions see committed state.

    `callback` may not assign reactive state directly; to write, it opens its own
    transaction(...). An exception from either function is logged and does not
    reach the code that made the change.

    For example, ``on_change(lambda: cart.total, update_badge)`` calls
    ``update_badge(total)`` after a commit changes the total, but not after one
    that swaps two items of the same price.

    Returns:
        The listener. Keep a reference to it and close it to stop the calls.
    """
    return ChangeListener(read, callback)
