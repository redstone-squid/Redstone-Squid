"""Deterministic interleaving controls for transaction state-machine tests."""

from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import squid_reactivity.core as core


class InterleavingHarness:
    """Run queued callbacks at named checkpoints. Leaving :meth:`installed` ends interception."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._scheduled: dict[str, deque[Callable[[], None]]] = defaultdict(deque)

    def at(self, checkpoint: str, callback: Callable[[], None]) -> None:
        """Queue ``callback`` for the next occurrence of ``checkpoint``."""
        self._scheduled[checkpoint].append(callback)

    def checkpoint(self, name: str) -> None:
        """Record a checkpoint and run its next queued callback, if any."""
        self.seen.append(name)
        scheduled = self._scheduled.get(name)
        if scheduled:
            scheduled.popleft()()

    @contextmanager
    def installed(self) -> Iterator[InterleavingHarness]:
        """Use this harness until the context exits, then restore the previous one."""
        token = core._INTERLEAVER.set(self.checkpoint)
        core._INTERLEAVER_USERS += 1
        try:
            yield self
        finally:
            core._INTERLEAVER_USERS -= 1
            core._INTERLEAVER.reset(token)


__all__ = ["InterleavingHarness"]
