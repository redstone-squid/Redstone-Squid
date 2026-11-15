"""Listen for changes to the reactive state a consumer read.

effect() and on_change() are the ready-made consumers. An effect reruns a function
when the state it read changes; on_change() calls a function when a computed value
changes. Subscriptions is the part underneath, for a consumer that decides for
itself when to recompute.
"""

import logging
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Self, override

from squid_reactivity._addresses import CellAddress, subscribe
from squid_reactivity._core import _equal
from squid_reactivity._delivery import schedule
from squid_reactivity._tracking import Dependencies, capture_reads, observe_reads
from squid_reactivity.topics import DependencyAddress, Topic
from squid_reactivity.transactions._context import continuation_callback
from squid_reactivity.transactions._state import _CURRENT_TRANSACTION

_log = logging.getLogger(__name__)


def _name(function: Callable[..., object]) -> str:
    return getattr(function, "__qualname__", None) or repr(function)


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


class _QueuedConsumer:
    """Subscribe to what a function read and queue a rerun when any of it changes.

    Subclasses implement _deliver(), which the delivery queue calls once per pass
    however many notifications arrived, and describe(), which names the user's
    function in a log message. See `_delivery` for when the queue runs.
    """

    def __init__(self) -> None:
        self._closed = False
        self._dependencies = Dependencies()
        self._subscriptions = Subscriptions(self._notified)

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
        schedule(self._deliver)

    def _deliver(self) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        """Name this listener and the user function behind it, for log messages."""
        raise NotImplementedError


class ChangeListener[ValueT](_QueuedConsumer):
    """A running on_change() registration. Close it to stop the calls.

    Models hold their listeners weakly, so a listener that nothing refers to stops
    without being closed. Keep it for as long as you want the calls.
    """

    def __init__(self, read: Callable[[], ValueT], callback: Callable[[ValueT], None]) -> None:
        super().__init__()
        self._read = read
        self._callback = callback
        # Committed state, also when created inside a transaction that may yet roll back.
        with _CURRENT_TRANSACTION.set(None), observe_reads() as dependencies:
            self._value = read()
        self._dependencies = dependencies
        self._subscribe()

    @override
    def describe(self) -> str:
        return f"on_change callback {_name(self._callback)}"

    @override
    def _deliver(self) -> None:
        # Several notifications queue this listener once. One that was a false alarm,
        # such as a write under a computed whose value stayed the same, stops here.
        if self._closed or self._dependencies.current():
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
        with continuation_callback():
            try:
                self._callback(value)
            except Exception:
                _log.exception("an on_change callback failed")


class Effect(_QueuedConsumer):
    """A running effect() registration. Close it to stop the effect.

    Models hold their effects weakly, so an effect that nothing refers to stops
    without being closed. Keep it for as long as the effect should run.
    """

    def __init__(self, run: Callable[[], Callable[[], None] | None]) -> None:
        super().__init__()
        self._run = run
        self._started = False
        self._cleanup: Callable[[], None] | None = None
        schedule(self._deliver)

    @override
    def close(self) -> None:
        """Stop the effect and run the cleanup its last run returned, if any."""
        super().close()
        self._clean_up()

    @override
    def describe(self) -> str:
        return f"effect {_name(self._run)}"

    @override
    def _deliver(self) -> None:
        if self._closed or (self._started and self._dependencies.current()):
            return
        self._started = True
        self._clean_up()
        with capture_reads() as dependencies:
            try:
                cleanup = self._run()
                if cleanup is not None and not callable(cleanup):
                    # Most often a function that returns a value, which on_change() is for,
                    # or an async function, whose coroutine would never be awaited.
                    message = f"an effect returns a cleanup function or None, not {type(cleanup).__name__}"
                    raise TypeError(message)  # noqa: TRY301
                self._cleanup = cleanup
            except Exception:
                # What the run read before failing is still subscribed, so a change retries.
                _log.exception("an effect failed")
        self._dependencies = dependencies
        self._subscribe()
        # A run that assigned something it had already read is out of date as it ends. Its
        # first run was not subscribed yet to hear that, so check instead of relying on it.
        if not self._dependencies.current():
            schedule(self._deliver)

    def _clean_up(self) -> None:
        cleanup, self._cleanup = self._cleanup, None
        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                _log.exception("an effect cleanup failed")


def on_change[ValueT](read: Callable[[], ValueT], callback: Callable[[ValueT], None]) -> ChangeListener[ValueT]:
    """Call `callback` with the new value whenever the result of `read` changes.

    `read` runs once now, to learn what it depends on, and again after a change to
    anything it read. It is read-only, like a computed function. `callback` runs only
    when the new result differs from the last one, so a write that leaves the result
    equal is not reported. It is not called for the initial value.

    One transaction produces at most one call, and one that rolls back produces
    none. The call is made after the commit has notified its other subscribers and
    run its result callbacks. Both functions see committed state.

    Listeners run one after another, in the order they were first notified, and
    never while the code that caused the change still has a transaction open.

    `callback` may not assign reactive state directly; to write, it opens its own
    transaction(...). Listeners affected by that transaction run after those
    already waiting, so each sees a cause before its consequences. A listener
    that reruns 100 times for one change is treated as a loop: an error naming
    the callback is logged and its remaining runs for that change are skipped.
    It stays subscribed and runs again after the next change.

    An exception from either function is logged and does not reach the code that
    made the change.

    For example, ``on_change(lambda: cart.total, update_badge)`` calls
    ``update_badge(total)`` after a commit changes the total, but not after one
    that swaps two items of the same price.

    Returns:
        The listener. Keep a reference to it and close it to stop the calls.
    """
    return ChangeListener(read, callback)


def effect(run: Callable[[], Callable[[], None] | None]) -> Effect:
    """Run `run` now, and again whenever reactive state it read has changed.

    Each run records what it read, so an effect follows the branch it took. Unlike
    a computed function or the `read` of on_change(), an effect may assign reactive
    state. Each assignment publishes at once unless the effect opens a transaction.

    `run` returns None, or a function that cleans up after that run. The cleanup is
    called before the next run and when the effect is closed. To act on a value a
    function returns, use on_change() instead.

    An effect never runs while the code that created or triggered it has a
    transaction open; it runs when that transaction ends, after the commit has
    notified its subscribers and run its result callbacks. Effects and on_change()
    listeners share one queue and run in the order they were first notified.

    An effect that assigns something it reads runs again, until a run changes
    nothing. One that reruns 100 times for a single change is treated as a loop:
    an error naming `run` is logged and its remaining runs for that change are
    skipped. The effect stays active and runs again after the next change to what
    it last read. An exception from `run` is logged; the effect reruns after the
    next change to what it read before failing.

    For example, this keeps a stored total in step with two fields::

        def update_total() -> None:
            cart.total = cart.apples + cart.pears


        totals = effect(update_total)

    Returns:
        The effect. Keep a reference to it and close it to stop it.
    """
    return Effect(run)
