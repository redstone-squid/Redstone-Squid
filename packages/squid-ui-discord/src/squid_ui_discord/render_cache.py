"""Bounded callback-free constructor-program caching for Discord renderers."""

from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass


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
    """A bounded LRU of callback-free Discord constructor programs."""

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

    def put(self, key: Hashable, program: object, *, certified: bool) -> None:
        """Retain one successfully executed program and its strongest certificate."""
        previous = self._entries.get(key)
        if previous is not None:
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
        """Discard every retained program and audit certificate."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
