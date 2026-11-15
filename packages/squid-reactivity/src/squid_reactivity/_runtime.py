"""Bookkeeping shared by tasks on one thread, independent of copied task contexts."""

import uuid
from collections import deque
from collections.abc import Callable
from threading import local


class _ThreadState(local):
    def __init__(self) -> None:
        self.identity = uuid.uuid4()
        self.sequence = 0
        self.epoch = 0
        self.listeners: deque[Callable[[], None]] = deque()
        """Change listeners waiting to run, in the order they were first notified. See `_delivery`."""
        self.queued: set[Callable[[], None]] = set()
        """The members of `listeners`, for constant-time duplicate checks."""
        self.holds = 0
        """Open `_delivery.hold()` blocks, plus one while the queue is running."""


_runtime = _ThreadState()


def _bump_epoch() -> None:
    """Invalidate this thread's computed-cache fast path after a state change."""
    _runtime.epoch += 1
