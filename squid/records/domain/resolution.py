"""Compare record candidates in priority order.

Missing primary metrics exclude a candidate. Missing tie-breakers leave a result
unresolved only when the comparison reaches them; an earlier decisive metric
needs no lower-priority evidence.
"""

from collections.abc import Iterable, Sequence

from squid.records.domain.models import (
    Candidate,
    CandidateGap,
    ResolutionStatus,
    TimingReduction,
    TimingVariant,
    TitleResolution,
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


def resolve_smallest(candidates: Iterable[Candidate]) -> TitleResolution:
    """Resolve the fixed-volume record, consulting completion only for a tie."""
    active = _smallest_pool(candidates)
    if not active:
        return TitleResolution(status=ResolutionStatus.NO_CANDIDATE)

    if len(active) == 1:
        return _resolved(active)
    return _resolve_completion_tie(active)


def resolve_fastest(candidates: Iterable[Candidate], methods: Sequence[str]) -> TitleResolution:
    """Compare each build's slowest variant, using lower-priority methods only for ties.

    `methods` names timing slots in priority order and supplies gap field names.
    After every timing method ties, earliest completion breaks the tie; identical
    dates produce co-holders.
    """
    active, incomplete = _fastest_pool(candidates, methods)
    if incomplete is not None:
        return incomplete
    if len(active) == 1:
        return _resolved(active)
    return _resolve_completion_tie(active)


def resolve_first(candidates: Iterable[Candidate]) -> TitleResolution:
    """Resolve the earliest provable completion date."""
    eligible = [candidate for candidate in candidates if candidate.completion_at is not None]
    if not eligible:
        return TitleResolution(status=ResolutionStatus.NO_CANDIDATE)

    earliest = min(candidate.completion_at for candidate in eligible if candidate.completion_at is not None)
    return _resolved(candidate for candidate in eligible if candidate.completion_at == earliest)


def resolve_fastest_smallest(
    candidates: Iterable[Candidate],
    methods: Sequence[str],
) -> TitleResolution:
    """Resolve the fastest build in the minimum-volume pool."""
    active = _smallest_pool(candidates)
    if not active:
        return TitleResolution(status=ResolutionStatus.NO_CANDIDATE)
    return resolve_fastest(active, methods)


def resolve_smallest_fastest(
    candidates: Iterable[Candidate],
    methods: Sequence[str],
) -> TitleResolution:
    """Resolve the smallest build in the fastest-timing pool."""
    active, incomplete = _fastest_pool(candidates, methods)
    if incomplete is not None:
        return incomplete
    return resolve_smallest(active)


def _smallest_pool(candidates: Iterable[Candidate]) -> list[Candidate]:
    eligible = [candidate for candidate in candidates if candidate.fixed_volume is not None]
    if not eligible:
        return []
    minimum = min(candidate.fixed_volume for candidate in eligible if candidate.fixed_volume is not None)
    return [candidate for candidate in eligible if candidate.fixed_volume == minimum]


def _fastest_pool(
    candidates: Iterable[Candidate],
    methods: Sequence[str],
) -> tuple[list[Candidate], TitleResolution | None]:
    candidate_reductions = [(candidate, reduce_timing_variants(candidate.timing_variants)) for candidate in candidates]
    active = [
        (candidate, reduction)
        for candidate, reduction in candidate_reductions
        if reduction.timing is not None and reduction.timing.values[0] is not None
    ]
    if not active:
        return [], TitleResolution(status=ResolutionStatus.NO_CANDIDATE)

    for index, method in enumerate(methods):
        if len(active) == 1:
            return [active[0][0]], None

        missing = [
            CandidateGap(candidate.build_id, method)
            for candidate, reduction in active
            if reduction.timing is None
            or index >= len(reduction.timing.values)
            or reduction.timing.values[index] is None
        ]
        if missing:
            return (
                [],
                TitleResolution(
                    status=ResolutionStatus.UNRESOLVED,
                    provisional_holder_ids=_candidate_ids(candidate for candidate, _ in active),
                    gaps=tuple(missing),
                ),
            )

        known_values = [
            value
            for _, reduction in active
            if reduction.timing is not None
            for value in (reduction.timing.values[index],)
            if value is not None
        ]
        minimum = min(known_values)
        active = [
            (candidate, reduction)
            for candidate, reduction in active
            if reduction.timing is not None and reduction.timing.values[index] == minimum
        ]

    return [candidate for candidate, _ in active], None


def _resolve_completion_tie(candidates: Sequence[Candidate]) -> TitleResolution:
    missing = tuple(
        CandidateGap(candidate.build_id, "completion_at") for candidate in candidates if candidate.completion_at is None
    )
    if missing:
        return TitleResolution(
            status=ResolutionStatus.UNRESOLVED,
            provisional_holder_ids=_candidate_ids(candidates),
            gaps=missing,
        )

    earliest = min(candidate.completion_at for candidate in candidates if candidate.completion_at is not None)
    return _resolved(candidate for candidate in candidates if candidate.completion_at == earliest)


def _resolved(candidates: Iterable[Candidate]) -> TitleResolution:
    return TitleResolution(
        status=ResolutionStatus.RESOLVED,
        holder_ids=_candidate_ids(candidates),
    )


def _candidate_ids(candidates: Iterable[Candidate]) -> tuple[int, ...]:
    return tuple(sorted(candidate.build_id for candidate in candidates))
