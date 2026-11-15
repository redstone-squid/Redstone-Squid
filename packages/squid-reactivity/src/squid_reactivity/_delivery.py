"""Call change listeners after the change that notified them has been fully announced.

A commit notifies its subscribers one at a time. A listener called in the middle of
that could commit a transaction of its own, and the remaining subscribers would hear
about this second commit before the first. Listeners are therefore queued. The queue
runs when nothing on this thread is still announcing a change and the code that
caused the change has no transaction open.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from squid_reactivity._runtime import _runtime
from squid_reactivity._tracking import _DEPENDENCY_COLLECTOR
from squid_reactivity.transactions._state import _CURRENT_TRANSACTION, _READ_CONSISTENCY, _READ_ONLY, _ReadConsistency

_log = logging.getLogger(__name__)

_MAX_RUNS = 100
"""How often one listener may run in a single pass before its changes count as a loop."""


def schedule(run: Callable[[], None]) -> None:
    """Queue a listener, once, and run the queue now if nothing is being announced."""
    # Bound methods compare by (instance, function), so each listener queues once.
    if run not in _runtime.queued:
        _runtime.queued.add(run)
        _runtime.listeners.append(run)
    deliver()


@contextmanager
def hold() -> Iterator[None]:
    """Keep queued listeners waiting until this block exits. Blocks may nest."""
    _runtime.holds += 1
    try:
        yield
    finally:
        _runtime.holds -= 1
        deliver()


def deliver() -> None:
    """Run queued listeners in the order they were first notified.

    A listener that changes state queues the listeners it affects behind those already
    waiting, so each one sees a cause before its consequences. Nothing runs while a
    hold() block is open or while the calling code has a transaction open; that
    transaction runs the queue when it finishes.

    The queue can start inside whatever code made the last change: a computed function
    initializing an object, or a task that inherited a finished transaction. Listeners
    do not belong to that code, so they run without its transaction, read policy,
    write restriction, or dependency capture.
    """
    if _runtime.holds:
        return
    current = _CURRENT_TRANSACTION.get()
    if current is not None and not current.closed:
        return
    # Held while running, so a commit made by a listener queues behind this pass
    # and does not start a nested one.
    _runtime.holds += 1
    try:
        with (
            _CURRENT_TRANSACTION.set(None),
            _READ_CONSISTENCY.set(_ReadConsistency.DEFAULT),
            _READ_ONLY.set(None),
            _DEPENDENCY_COLLECTOR.set(None),
        ):
            _run_queue()
    finally:
        _runtime.holds -= 1


def _run_queue() -> None:
    runs: dict[Callable[[], None], int] = {}
    while _runtime.listeners:
        run = _runtime.listeners.popleft()
        # Forget it before running, so a change made by `run` can queue it again.
        _runtime.queued.discard(run)
        runs[run] = runs.get(run, 0) + 1
        if runs[run] == _MAX_RUNS + 1:
            _log.error(
                "a change listener or effect ran %d times in response to one change and was "
                "stopped; it, or another one, keeps changing what it reads",
                _MAX_RUNS,
            )
        if runs[run] <= _MAX_RUNS:
            run()
