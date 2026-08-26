"""Small per-runtime LRU for callback-free resolved plan structure."""

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from squid_ui import scene
from squid_ui.runtime.presentation import SessionUpdate
from squid_ui.scene.model import PlanReport


@dataclass(frozen=True, slots=True)
class CachedPlan:
    scene: scene.Document[Any]
    report: PlanReport
    session_updates: tuple[SessionUpdate, ...] = ()
    """Replayed on a hit: the session is part of the key, so these stay correct."""
    strategies: tuple[tuple[str, str], ...] = ()
    states_explored: int = 0
    search_fallback: bool = False
    variant_positions: tuple[tuple[tuple[int | str, ...], int], ...] = ()
    fallbacks: tuple[tuple[str, int], ...] = ()
    """Fallback decisions for entries whose selected primitive tree could not be compiled."""
    lowered_template: object | None = None
    """Selected primitive tree with every process-local value replaced by a document slot."""


class PlanCache:
    """A deliberately small LRU; runtimes do not retain unbounded document history."""

    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1:
            message = "plan cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self._entries: OrderedDict[str, CachedPlan] = OrderedDict()
        self._incremental: OrderedDict[str, None] = OrderedDict()

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

    def admits_incremental(self, key: str) -> bool:
        """Whether a prior lossless plan certified this configuration and region shape."""
        if key not in self._incremental:
            return False
        self._incremental.move_to_end(key)
        return True

    def certify_incremental(self, key: str) -> None:
        """Record one lossless, uncoupled region shape as eligible for local replanning."""
        self._incremental[key] = None
        self._incremental.move_to_end(key)
        while len(self._incremental) > self.capacity:
            self._incremental.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class PlanMemo:
    """One runtime's callback-bearing exact result; :meth:`clear` ends its retention.

    This is deliberately separate from :class:`PlanCache`: the latter is safe to share because
    it is callback-free, while this memo retains only the current render of one owner.
    """

    def __init__(self) -> None:
        self._source: object | None = None
        self._key: object | None = None
        self._session: object | None = None
        self._session_revisions: set[int] = set()
        self._result: object | None = None

    def get(self, source: object, key: object, session: object, session_revision: int) -> object | None:
        if (
            self._source is source
            and self._key == key
            and self._session is session
            and session_revision in self._session_revisions
        ):
            return self._result
        return None

    def put(self, source: object, key: object, session: object, session_revision: int, result: object) -> None:
        self._source = source
        self._key = key
        self._session = session
        self._session_revisions = {session_revision}
        self._result = result

    def promote(self, session: object, session_revision: int) -> None:
        """Accept the post-commit revision of the session this result already describes."""
        if self._session is session and self._result is not None:
            self._session_revisions.add(session_revision)

    def clear(self) -> None:
        self._source = None
        self._key = None
        self._session = None
        self._session_revisions.clear()
        self._result = None
