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
from squid_reactivity.transactions._state import _CURRENT_TRANSACTION

_log = logging.getLogger(__name__)

_MAX_RUNS = 100
"""How often one listener may run in a single pass before its changes count as a loop."""


def schedule(run: Callable[[], None]) -> None:
    """Queue a listener, once, and run the queue now if nothing is being announced."""
    if run not in _runtime.listeners:
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
    """
    if _runtime.holds:
        return
    current = _CURRENT_TRANSACTION.get()
    if current is not None and not current.closed:
        return
    runs: dict[Callable[[], None], int] = {}
    # Held while running, so a commit made by a listener queues behind this pass
    # and does not start a nested one.
    _runtime.holds += 1
    try:
        while _runtime.listeners:
            run = _runtime.listeners.popleft()
            runs[run] = runs.get(run, 0) + 1
            if runs[run] == _MAX_RUNS + 1:
                _log.error(
                    "a change listener ran %d times in response to one change and was stopped; "
                    "its callback, or another listener's, keeps changing what it reads",
                    _MAX_RUNS,
                )
            if runs[run] <= _MAX_RUNS:
                run()
    finally:
        _runtime.holds -= 1
