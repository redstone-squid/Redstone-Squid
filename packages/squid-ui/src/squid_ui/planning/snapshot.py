"""One owner's planning inputs, candidate result, committed result, and fingerprints."""

from dataclasses import dataclass, replace
from typing import Any

from squid_ui import scene
from squid_ui.planning.identity import FingerprintCache
from squid_ui.planning.result import PlanMetrics, PlanResult, PlanReuse
from squid_ui.presentation import PresentationState


@dataclass(frozen=True, slots=True)
class _Generation[BodyT: scene.Body]:
    source: object
    environment: object
    presentation: PresentationState
    revision: int
    result: PlanResult[BodyT]


@dataclass(frozen=True, slots=True)
class SnapshotStats:
    hits: int
    misses: int
    last_miss: str | None


class PlanningSnapshot[BodyT: scene.Body = Any]:
    """Coordinate exact reuse without confusing computed output with delivered output.

    A failed delivery may reuse the candidate computation, but only commit advances
    the displayed generation. Disabling reuse supplies a cold reference path while
    retaining the same presentation history and lifecycle behavior.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._hits = self._misses = 0
        self._last_miss: str | None = None
        self.fingerprints = FingerprintCache()
        self._pending: _Generation[BodyT] | None = None
        self._committed: _Generation[BodyT] | None = None

    @property
    def committed(self) -> PlanResult[BodyT] | None:
        return None if self._committed is None else self._committed.result

    def replay(self, source: object, environment: object, session: PresentationState) -> PlanResult[BodyT] | None:
        self._misses += 1
        self._last_miss = "disabled" if not self.enabled else "source"
        if not self.enabled:
            return None
        for generation in (self._pending, self._committed):
            if generation is None or generation.source is not source:
                continue
            if generation.environment != environment:
                self._last_miss = "environment"
                continue
            if generation.presentation is not session or generation.revision != session.revision:
                self._last_miss = "presentation"
                continue
            self._hits += 1
            self._misses -= 1
            self._last_miss = None
            result = replace(generation.result, metrics=PlanMetrics(cache_hit=True, reuse=PlanReuse.EXACT))
            self._pending = replace(generation, result=result)
            return result
        return None

    def stage(self, source: object, environment: object, session: PresentationState, result: PlanResult[BodyT]) -> None:
        self._pending = _Generation(source, environment, session, session.revision, result)

    def commit(self, result: PlanResult[BodyT], session: PresentationState) -> None:
        """Accept only the candidate actually presented, after its session updates apply."""
        pending = self._pending
        if pending is not None and pending.result is result and pending.presentation is session:
            self._committed = replace(pending, revision=session.revision)
        else:
            # Spread planning or a superseded candidate has no matching exact input.
            self._committed = None
        self._pending = None

    def statistics(self) -> SnapshotStats:
        return SnapshotStats(self._hits, self._misses, self._last_miss)

    def clear(self) -> None:
        self._pending = self._committed = None
        self.fingerprints.clear()
