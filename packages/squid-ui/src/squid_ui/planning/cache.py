"""Reuse planning work while keeping callbacks tied to the render that supplied them.

PlanMemo retains one owner's complete result and replays it only for the same inputs
and presentation-state revision. PlanCache stores partition hints without live callbacks;
the planner certifies current content and rebuilds its bindings when trying those cuts.
This lets an identical-looking display reuse layout work without invoking an older
component's handler when a control is pressed.
"""

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from squid_ui import scene
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.result import PlanResult, PlanReuse
from squid_ui.presentation import PresentationState


class PlanCache:
    """Retain bounded, callback-free partition hints for shared composition.

    A hint suggests page boundaries; the planner certifies them against current
    content and target limits. Writes enforce capacity, evicting least-used hints.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1:
            message = "plan cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self._partitions: OrderedDict[str, Mapping[OccurrenceId, tuple[int, ...]]] = OrderedDict()

    def partition_hint(self, key: str) -> Mapping[OccurrenceId, tuple[int, ...]] | None:
        """Return prior cuts as a search hint, never as a certificate of feasibility."""
        try:
            cuts = self._partitions[key]
        except KeyError:
            return None
        self._partitions.move_to_end(key)
        return cuts

    def remember_partitions(self, key: str, cuts: Mapping[OccurrenceId, tuple[int, ...]]) -> None:
        self._partitions[key] = MappingProxyType(dict(cuts))
        self._partitions.move_to_end(key)
        while len(self._partitions) > self.capacity:
            self._partitions.popitem(last=False)

    def __len__(self) -> int:
        return len(self._partitions)


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

    def replay(self, source: object, key: object, session: PresentationState) -> PlanResult[BodyT] | None:
        """Return a matching retained result with metrics marking this call as an exact hit."""
        if (
            self._source is source
            and self._key == key
            and self._session is session
            and session.revision in self._session_revisions
            and self._result is not None
        ):
            return replace(self._result, metrics=replace(self._result.metrics, cache_hit=True, reuse=PlanReuse.EXACT))
        return None

    def store(self, source: object, key: object, session: PresentationState, result: PlanResult[BodyT]) -> None:
        """Retain one exact result against the session's current revision."""
        self._source = source
        self._key = key
        self._session = session
        self._session_revisions = {session.revision}
        self._result = result

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
