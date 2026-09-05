"""Small per-runtime LRU for callback-free resolved plan structure."""

from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

from squid_ui import scene
from squid_ui.runtime.presentation_state import PresentationState, SessionUpdate
from squid_ui.scene.model import PlanReport, PlanResult, PlanReuse


@dataclass(frozen=True, slots=True)
class CachedPlan[BodyT: scene.Body = scene.Body]:
    """One `PlanCache` entry: the planned scene plus the decisions needed to rebuild a `PlanResult` from it.

    Holds no callbacks, so one entry serves every owner whose request digests to the same key.
    """

    scene: scene.Scene[BodyT]
    report: PlanReport
    session_updates: tuple[SessionUpdate, ...] = ()
    """Replayed on a hit: the session is part of the key, so these stay correct."""
    strategies: tuple[tuple[str, str], ...] = ()
    """Selected strategy id per semantic path; a hit re-lowers with these pinned instead of searching."""
    states_explored: int = 0
    search_fallback: bool = False
    """Both copied into the hit's metrics, so a replay reports the search that produced it."""
    variant_positions: tuple[tuple[tuple[int | str, ...], int], ...] = ()
    """Selected rung per `Variants` ladder path, re-spliced with `frontier.resolve_variants` on a hit."""
    fallbacks: tuple[tuple[str, int], ...] = ()
    """Fallback decisions for entries whose selected primitive tree could not be compiled."""
    lowered_template: object | None = None
    """Selected primitive tree with every process-local value replaced by a document slot."""


class PlanCache[BodyT: scene.Body = Any]:
    """LRU of callback-free plans keyed by the `PlanRequest.cache_context` digest, `capacity` entries deep.

    Safe to share across owners. `BodyT` defaults to `Any` because the constructor has no
    target to infer it from; the first planner call binds it. Raises `ValueError` when
    `capacity` is below one.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1:
            message = "plan cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self._entries: OrderedDict[str, CachedPlan[BodyT]] = OrderedDict()
        self._incremental: OrderedDict[str, None] = OrderedDict()

    def get(self, key: str) -> CachedPlan[BodyT] | None:
        """The entry under `key`, refreshed as most recent, or `None` on a miss."""
        value = self._entries.get(key)
        if value is not None:
            self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: CachedPlan[BodyT]) -> None:
        """Insert or refresh `key`, evicting the least recently used entries past `capacity`."""
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


class PlanMemo[BodyT: scene.Body = Any]:
    """One owner's most recent exact `PlanResult`, callbacks included; `clear()` drops it.

    Not shareable, unlike `PlanCache`: the result holds the owner's live callbacks, and a hit
    requires the same `source` object, an equal key, the same session object, and a session
    revision this memo has accepted.
    """

    def __init__(self) -> None:
        self._source: object | None = None
        self._key: object | None = None
        self._session: PresentationState | None = None
        self._session_revisions: set[int] = set()
        self._result: PlanResult[BodyT] | None = None

    def get(
        self, source: object, key: object, session: PresentationState, session_revision: int
    ) -> PlanResult[BodyT] | None:
        """The retained result as stored, or `None` unless every part of the key matches."""
        if (
            self._source is source
            and self._key == key
            and self._session is session
            and session_revision in self._session_revisions
        ):
            return self._result
        return None

    def put(
        self,
        source: object,
        key: object,
        session: PresentationState,
        session_revision: int,
        result: PlanResult[BodyT],
    ) -> None:
        """Replace whatever is held; only `session_revision` hits until `promote` accepts another."""
        self._source = source
        self._key = key
        self._session = session
        self._session_revisions = {session_revision}
        self._result = result

    def replay(self, source: object, key: object, session: PresentationState) -> PlanResult[BodyT] | None:
        """The retained result with `cache_hit=True` and `PlanReuse.EXACT` in its metrics, or `None` on a miss.

        Re-marked here rather than by each backend, so a hit never reports the metrics of the
        search that produced it.
        """
        retained = self.get(source, key, session, session.revision)
        if retained is None:
            return None
        return replace(retained, metrics=replace(retained.metrics, cache_hit=True, reuse=PlanReuse.EXACT))

    def store(self, source: object, key: object, session: PresentationState, result: PlanResult[BodyT]) -> None:
        """Retain one exact result against the session's current revision."""
        self.put(source, key, session, session.revision, result)

    def promote(self, session: PresentationState, session_revision: int) -> None:
        """Accept the post-commit revision of the session this result already describes."""
        if self._session is session and self._result is not None:
            self._session_revisions.add(session_revision)

    def clear(self) -> None:
        self._source = None
        self._key = None
        self._session = None
        self._session_revisions.clear()
        self._result = None
