"""Compare record candidates in priority order.

A candidate without the primary metric does not compete. At every later
comparison, a candidate missing that fact loses to the candidates that have it,
and the result records the gap, because supplying the fact could make it win.
When no remaining candidate has the fact, the comparison is skipped and each of
them gets a gap, because the fact could separate them.

Candidates still level after every metric are ordered by completion date. The
earliest holds the record and the later ones are tied.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import assert_never

from squid.core.records.domain.models import (
    Candidate,
    CandidateGap,
    RecordCriterion,
    RecordResolution,
    ResolutionStatus,
    TimingReduction,
    TimingVariant,
)


def reduce_timing_variants(variants: Sequence[TimingVariant]) -> TimingReduction:
    """Select the slowest timing variant by comparing methods in priority order.

    Variants without a primary timing are excluded. At each position, retain only
    variants tied for the largest value. Thus (5, 2) is slower than (4, 8), even
    though its second method is faster. Missing data among the remaining variants
    stops comparison and leaves that position and the rest unknown.

    Once one variant remains, copy its remaining values, including unknowns.
    `missing_index` identifies a blocked comparison, not every unknown value in
    the returned timing.
    """
    active = [variant for variant in variants if variant.values[0] is not None]
    if not active:
        return TimingReduction(timing=None, missing_index=0)

    reduced: list[int | None] = []
    width = max(len(variant.values) for variant in active)
    for index in range(width):
        values = [variant.values[index] if index < len(variant.values) else None for variant in active]
        if any(value is None for value in values):
            reduced.extend([None] * (width - index))
            return TimingReduction(timing=TimingVariant(tuple(reduced)), missing_index=index)

        known_values = [value for value in values if value is not None]
        slowest = max(known_values)
        reduced.append(slowest)
        active = [variant for variant, value in zip(active, known_values, strict=True) if value == slowest]
        if len(active) == 1:
            only = active[0]
            reduced.extend(only.values[index + 1 :])
            reduced.extend([None] * (width - len(reduced)))
            return TimingReduction(timing=TimingVariant(tuple(reduced)))

    return TimingReduction(timing=TimingVariant(tuple(reduced)))


@dataclass(slots=True)
class _Contest:
    """The candidates still level on every metric compared so far, and the gaps found on the way."""

    active: list[Candidate]
    gaps: list[CandidateGap] = field(default_factory=list)

    def keep_best[T: (int, datetime)](self, name: str, value: Callable[[Candidate], T | None]) -> None:
        """Keep the candidates with the lowest known value; unknown values lose but leave gaps."""
        known = [(candidate, metric) for candidate in self.active if (metric := value(candidate)) is not None]
        if not known:
            if len(self.active) > 1:
                self.gaps.extend(CandidateGap(candidate.build_id, name) for candidate in self.active)
            return
        self.gaps.extend(CandidateGap(c.build_id, name) for c in self.active if value(c) is None)
        best = min(metric for _, metric in known)
        self.active = [candidate for candidate, metric in known if metric == best]

    def finish(self) -> RecordResolution:
        """Award the record to the earliest completion; later completions are tied."""
        if not self.active:
            return RecordResolution(ResolutionStatus.NO_CANDIDATE)
        level = self.active
        self.keep_best("completion_at", lambda candidate: candidate.completion_at)
        holders = {candidate.build_id for candidate in self.active}
        tied = (c.build_id for c in level if c.build_id not in holders and c.completion_at is not None)
        return RecordResolution(
            ResolutionStatus.RESOLVED,
            holder_ids=tuple(sorted(holders)),
            tied_ids=tuple(sorted(tied)),
            gaps=tuple(sorted(dict.fromkeys(self.gaps), key=lambda gap: (gap.build_id, gap.field))),
        )


def resolve_smallest(candidates: Iterable[Candidate]) -> RecordResolution:
    """Resolve the fixed-volume record."""
    return _smallest(_Contest(list(candidates))).finish()


def resolve_fastest(candidates: Iterable[Candidate], methods: Sequence[str]) -> RecordResolution:
    """Compare each build's slowest variant, using lower-priority methods only for ties.

    `methods` names timing slots in priority order and supplies gap field names.
    """
    return _fastest(_Contest(list(candidates)), methods).finish()


def resolve_first(candidates: Iterable[Candidate]) -> RecordResolution:
    """Resolve the earliest provable completion date; identical dates share the record."""
    result = _Contest([candidate for candidate in candidates if candidate.completion_at is not None]).finish()
    # Completion is this record's only metric, so later builds lost rather than tied.
    return replace(result, tied_ids=())


def resolve_fastest_smallest(candidates: Iterable[Candidate], methods: Sequence[str]) -> RecordResolution:
    """Resolve the fastest build in the minimum-volume pool."""
    return _fastest(_smallest(_Contest(list(candidates))), methods).finish()


def resolve_smallest_fastest(candidates: Iterable[Candidate], methods: Sequence[str]) -> RecordResolution:
    """Resolve the smallest build in the fastest-timing pool."""
    contest = _fastest(_Contest(list(candidates)), methods)
    contest.keep_best("fixed_volume", lambda candidate: candidate.fixed_volume)
    return contest.finish()


def _smallest(contest: _Contest) -> _Contest:
    # Volume is the primary metric, so builds without one do not compete at all.
    contest.active = [candidate for candidate in contest.active if candidate.fixed_volume is not None]
    contest.keep_best("fixed_volume", lambda candidate: candidate.fixed_volume)
    return contest


def _fastest(contest: _Contest, methods: Sequence[str]) -> _Contest:
    timings: dict[int, tuple[int | None, ...]] = {}
    for candidate in contest.active:
        reduction = reduce_timing_variants(candidate.timing_variants)
        if reduction.timing is not None and reduction.timing.values[0] is not None:
            values = reduction.timing.values
            timings[candidate.build_id] = values + (None,) * (len(methods) - len(values))
    contest.active = [candidate for candidate in contest.active if candidate.build_id in timings]
    for index, method in enumerate(methods):
        contest.keep_best(method, lambda candidate, index=index: timings[candidate.build_id][index])
    return contest


def resolve_record(
    criterion: RecordCriterion, candidates: Iterable[Candidate], methods: Sequence[str]
) -> RecordResolution:
    """Apply a record's comparison order using the kind's timing methods."""
    match criterion:
        case RecordCriterion.FIRST:
            return resolve_first(candidates)
        case RecordCriterion.SMALLEST:
            return resolve_smallest(candidates)
        case RecordCriterion.FASTEST:
            return resolve_fastest(candidates, methods)
        case RecordCriterion.FASTEST_SMALLEST:
            return resolve_fastest_smallest(candidates, methods)
        case RecordCriterion.SMALLEST_FASTEST:
            return resolve_smallest_fastest(candidates, methods)
        case _:
            assert_never(criterion)
