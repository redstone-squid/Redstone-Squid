"""Reuse planning work while keeping callbacks tied to the render that supplied them.

PlanMemo retains one owner's complete result and replays it only for the same inputs
and presentation-state revision. PlanCache stores structure without live callbacks;
the planner rebuilds bindings from the current document when reusing that structure.
This lets an identical-looking display reuse layout work without invoking an older
component's handler when a control is pressed.
"""

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any

from squid_ui import scene
from squid_ui.planning.degradation import DegradationProfile
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.result import PlanReport, PlanResult, PlanReuse
from squid_ui.presentation import PresentationState, SessionUpdate


@dataclass(frozen=True, slots=True)
class CachedPlan[BodyT: scene.Body = scene.Body]:
    scene: scene.Scene[BodyT]
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

    cost: ResourceCost = field(default=EMPTY_COST, kw_only=True)
    degradation: DegradationProfile = field(default_factory=DegradationProfile, kw_only=True)


class PlanCache[BodyT: scene.Body = Any]:
    """Reuse callback-free plans, evicting the least recently used entries when full.

    A separate store remembers isolated text-region shapes that previously fitted
    without loss. These certifications let the Discord planner try a single candidate
    before a full search when only that region's text changes.

    Each store holds at most `capacity` entries. Specialize `BodyT` when the owner
    knows its scene type; the default also supports runtime-selected targets.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1:
            message = "plan cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self._entries: OrderedDict[str, CachedPlan[BodyT]] = OrderedDict()
        self._incremental: OrderedDict[str, None] = OrderedDict()

    def get(self, key: str) -> CachedPlan[BodyT] | None:
        value = self._entries.get(key)
        if value is not None:
            self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: CachedPlan[BodyT]) -> None:
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
    """One runtime's callback-bearing exact result; :meth:`clear` ends its retention.

    This is deliberately separate from :class:`PlanCache`: the latter is safe to share because
    it is callback-free, while this memo retains only the current render of one owner.
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
        self._source = source
        self._key = key
        self._session = session
        self._session_revisions = {session_revision}
        self._result = result

    def replay(self, source: object, key: object, session: PresentationState) -> PlanResult[BodyT] | None:
        """Return a matching retained result with metrics marking this call as an exact hit."""
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
