"""Bounded callback-free constructor-program caching for Discord renderers."""

from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderProgramCacheSnapshot:
    """Counters are cumulative since construction; `certified` counts entries that skip the renderer's audit."""

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

    A `capacity` below 1 raises `ValueError`. A certified entry is one whose drawing passed the
    renderer's audit with a static view factory and no wiring, so the renderer draws it again
    without re-auditing.
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
        """`None` on a miss; a hit is moved to most-recently-used."""
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        self._entries.move_to_end(key)
        self._hits += 1
        return entry.program, entry.certified

    def put(self, key: Hashable, program: object, *, certified: bool) -> None:
        """A certificate once earned survives a later uncertified `put` of the same key."""
        previous = self._entries.get(key)
        if previous is not None:
            certified = certified or previous.certified
        self._entries[key] = _Entry(program, certified)
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
            self._evictions += 1

    def snapshot(self) -> RenderProgramCacheSnapshot:
        """Counters keep counting across `clear()`; only `entries` and `certified` drop."""
        return RenderProgramCacheSnapshot(
            entries=len(self._entries),
            certified=sum(entry.certified for entry in self._entries.values()),
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
        )

    def clear(self) -> None:
        """Drop every entry; the hit, miss and eviction counters keep their values."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
