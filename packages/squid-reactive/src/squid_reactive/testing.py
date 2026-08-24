"""Deterministic interleaving controls for transaction state-machine tests."""

from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from squid_reactive.core import _INTERLEAVER


class InterleavingHarness:
    """Run queued callbacks at named checkpoints. Leaving :meth:`installed` ends interception."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._scheduled: dict[str, deque[Callable[[], None]]] = defaultdict(deque)

    def at(self, checkpoint: str, callback: Callable[[], None]) -> None:
        """Queue ``callback`` for the next occurrence of ``checkpoint``."""
        self._scheduled[checkpoint].append(callback)

    def checkpoint(self, name: str) -> None:
        """Record a framework or author-defined yield point and run its next callback."""
        self.seen.append(name)
        scheduled = self._scheduled.get(name)
        if scheduled:
            scheduled.popleft()()

    @contextmanager
    def installed(self) -> Iterator[InterleavingHarness]:
        """Install this harness for the lexical test scope."""
        token = _INTERLEAVER.set(self.checkpoint)
        try:
            yield self
        finally:
            _INTERLEAVER.reset(token)


__all__ = ["InterleavingHarness"]
