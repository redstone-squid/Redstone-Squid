"""Bookkeeping shared by tasks on one thread, independent of copied task contexts."""

import uuid
from threading import local


class _ThreadState(local):
    def __init__(self) -> None:
        self.identity = uuid.uuid4()
        self.sequence = 0
        self.epoch = 0


_runtime = _ThreadState()


def _bump_epoch() -> None:
    """Invalidate this thread's computed-cache fast path after a state change."""
    _runtime.epoch += 1
