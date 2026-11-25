"""Reconstruct record ownership intervals from completion evidence."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from itertools import groupby

from squid.core.records.application.models import HolderHistoryEntry
from squid.core.records.domain.models import Candidate, RecordCriterion, ResolutionStatus
from squid.core.records.domain.resolution import resolve_record


def reconstruct_history(
    criterion: RecordCriterion,
    methods: Sequence[str],
    candidates: Sequence[Candidate],
) -> tuple[HolderHistoryEntry, ...]:
    """Reconstruct holder changes from today's facts and known completion dates.

    Builds completed at the same time enter together, and undated builds are
    omitted. These intervals are reconstructed evidence, not an audit log of
    previously published results.
    """
    dated = sorted(
        (candidate for candidate in candidates if candidate.completion_at is not None),
        key=_known_completion,
    )
    active: list[Candidate] = []
    periods: list[HolderHistoryEntry] = []
    for completion_at, group in groupby(dated, key=_known_completion):
        active.extend(group)
        resolution = resolve_record(criterion, active, methods)
        if resolution.status is not ResolutionStatus.RESOLVED:
            continue
        if periods and periods[-1].held_until is None and periods[-1].build_ids == resolution.holder_ids:
            continue
        if periods and periods[-1].held_until is None:
            previous = periods[-1]
            periods[-1] = replace(previous, held_until=completion_at)
        periods.append(
            HolderHistoryEntry(
                build_ids=resolution.holder_ids,
                held_from=completion_at,
                held_until=None,
            )
        )
    return tuple(periods)


def _known_completion(candidate: Candidate) -> datetime:
    assert candidate.completion_at is not None
    return candidate.completion_at
