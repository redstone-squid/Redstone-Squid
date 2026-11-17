"""Bounded storage for computations keyed by the identity of a retained input."""

from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CacheStats:
    hits: int
    misses: int
    entries: int
    retained_bytes: int
    evictions: int


class IdentityCache[ValueT]:
    """Keep keys alive to prevent ID recycling; callers own admission and byte estimates."""

    def __init__(self, *, capacity: int, max_bytes: int) -> None:
        if capacity < 1 or max_bytes < 1:
            message = "cache bounds must be positive"
            raise ValueError(message)
        self.capacity, self.max_bytes = capacity, max_bytes
        self._entries: OrderedDict[int, tuple[object, ValueT, int]] = OrderedDict()
        self._bytes = self._hits = self._misses = self._evictions = 0

    def get(self, key: object) -> ValueT | None:
        entry = self._entries.get(id(key))
        if entry is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries.move_to_end(id(key))
        return entry[1]

    def put(self, key: object, value: ValueT, *, weight: int) -> None:
        if weight > self.max_bytes:
            return
        old = self._entries.pop(id(key), None)
        if old is not None:
            self._bytes -= old[2]
        self._entries[id(key)] = (key, value, weight)
        self._bytes += weight
        while len(self._entries) > self.capacity or self._bytes > self.max_bytes:
            _, (_, _, removed) = self._entries.popitem(last=False)
            self._bytes -= removed
            self._evictions += 1

    def snapshot(self) -> CacheStats:
        return CacheStats(self._hits, self._misses, len(self._entries), self._bytes, self._evictions)

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0
