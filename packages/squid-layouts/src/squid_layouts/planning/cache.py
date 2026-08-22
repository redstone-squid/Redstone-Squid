"""Small per-runtime LRU for callback-free resolved plan structure."""

from collections import OrderedDict
from dataclasses import dataclass

from squid_layouts.runtime.presentation import SessionUpdate
from squid_layouts.scene.model import PlanReport, SceneDocument


@dataclass(frozen=True, slots=True)
class CachedPlan:
    scene: SceneDocument
    report: PlanReport
    session_updates: tuple[SessionUpdate, ...] = ()
    """Replayed on a hit: the session is part of the key, so these stay correct."""
    strategies: tuple[tuple[str, str], ...] = ()
    states_explored: int = 0
    search_fallback: bool = False
    variant_positions: tuple[tuple[tuple[int | str, ...], int], ...] = ()
    fallbacks: tuple[tuple[str, int], ...] = ()
    """All three decision classes, so a hit re-lowers the winner instead of searching again."""


class PlanCache:
    """A deliberately small LRU; runtimes do not retain unbounded document history."""

    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1:
            message = "plan cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self._entries: OrderedDict[str, CachedPlan] = OrderedDict()

    def get(self, key: str) -> CachedPlan | None:
        value = self._entries.get(key)
        if value is not None:
            self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: CachedPlan) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
