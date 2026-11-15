"""Reuse the steps for drawing a scene while creating fresh Discord objects each time.

A drawing program records constructors and scene values, without mounted callbacks.
Renderers execute it with the current plan and wiring to build a new view. A certified
program has also passed the renderer's audit, allowing unchanged static draws to skip
that check. Native extensions and custom wiring still need a fresh audit.
"""

from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass

from squid_ui.errors import DrawInvariantError


@dataclass(frozen=True, slots=True)
class RenderProgramCacheSnapshot:
    """One immutable view of renderer-program cache activity."""

    entries: int
    certified: int
    hits: int
    misses: int
    evictions: int


@dataclass(slots=True)
class _Entry:
    program: object
    certified: bool


class RenderProgramCache:
    """A bounded LRU of callback-free Discord constructor programs.

    Callers must key every input that affects the program and its audit result. Certificates
    belong to a program instance: replacing it under the same key requires a new audit.
    Cache entries never hold mounted callbacks.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1:
            message = "render program cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: Hashable) -> tuple[object, bool] | None:
        """Return a program and its audit certificate, recording its disposition."""
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        self._entries.move_to_end(key)
        self._hits += 1
        return entry.program, entry.certified

    def resolve[ProgramT](
        self, key: Hashable, program_type: type[ProgramT], compile: Callable[[], ProgramT]
    ) -> tuple[ProgramT, bool]:
        """Reuse a typed program or compile one without retaining it yet.

        Call `put` only after drawing and any required audit succeed. A failed
        construction must not leave a new program or certificate in the cache.
        """
        cached = self.get(key)
        if cached is None:
            return compile(), False
        program, certified = cached
        if not isinstance(program, program_type):
            message = "render program cache returned the wrong program type"
            raise DrawInvariantError(message)
        return program, certified

    def put(self, key: Hashable, program: object, *, certified: bool) -> None:
        """Retain a program, preserving its certificate only when the instance is unchanged."""
        previous = self._entries.get(key)
        if previous is not None and previous.program is program:
            certified = certified or previous.certified
        self._entries[key] = _Entry(program, certified)
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
            self._evictions += 1

    def snapshot(self) -> RenderProgramCacheSnapshot:
        """Return bounded cache size and cumulative disposition counters."""
        return RenderProgramCacheSnapshot(
            entries=len(self._entries),
            certified=sum(entry.certified for entry in self._entries.values()),
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
        )

    def clear(self) -> None:
        """Discard programs and certificates while retaining cumulative counters."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
