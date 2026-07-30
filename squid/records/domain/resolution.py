"""Progressive record comparison algorithms."""

from collections.abc import Iterable, Sequence

from squid.records.domain.models import (
    CandidateGap,
    RecordCandidate,
    RecordResolution,
    ResolutionStatus,
    TimingReduction,
    TimingVariant,
)


def reduce_timing_variants(variants: Sequence[TimingVariant]) -> TimingReduction:
    """Reduce possible behaviors to the lexicographically slowest provable one."""
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


def resolve_smallest(candidates: Iterable[RecordCandidate]) -> RecordResolution:
    """Resolve the fixed-volume record, consulting completion only for a tie."""
    eligible = [candidate for candidate in candidates if candidate.fixed_volume is not None]
    if not eligible:
        return RecordResolution(status=ResolutionStatus.NO_CANDIDATE)

    minimum = min(candidate.fixed_volume for candidate in eligible if candidate.fixed_volume is not None)
    active = [candidate for candidate in eligible if candidate.fixed_volume == minimum]
    if len(active) == 1:
        return _resolved(active)
    return _resolve_completion_tie(active)


def resolve_fastest(candidates: Iterable[RecordCandidate], methods: Sequence[str]) -> RecordResolution:
    """Resolve speed lexicographically, requesting lower methods only for ties."""
    candidate_reductions = [(candidate, reduce_timing_variants(candidate.timing_variants)) for candidate in candidates]
    active = [
        (candidate, reduction)
        for candidate, reduction in candidate_reductions
        if reduction.timing is not None and reduction.timing.values[0] is not None
    ]
    if not active:
        return RecordResolution(status=ResolutionStatus.NO_CANDIDATE)

    for index, method in enumerate(methods):
        if len(active) == 1:
            return _resolved([active[0][0]])

        missing = [
            CandidateGap(candidate.build_id, method)
            for candidate, reduction in active
            if reduction.timing is None
            or index >= len(reduction.timing.values)
            or reduction.timing.values[index] is None
        ]
        if missing:
            return RecordResolution(
                status=ResolutionStatus.UNRESOLVED,
                provisional_holder_ids=_candidate_ids(candidate for candidate, _ in active),
                gaps=tuple(missing),
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

    return _resolve_completion_tie([candidate for candidate, _ in active])


def _resolve_completion_tie(candidates: Sequence[RecordCandidate]) -> RecordResolution:
    missing = tuple(
        CandidateGap(candidate.build_id, "completion_at") for candidate in candidates if candidate.completion_at is None
    )
    if missing:
        return RecordResolution(
            status=ResolutionStatus.UNRESOLVED,
            provisional_holder_ids=_candidate_ids(candidates),
            gaps=missing,
        )

    earliest = min(candidate.completion_at for candidate in candidates if candidate.completion_at is not None)
    return _resolved(candidate for candidate in candidates if candidate.completion_at == earliest)


def _resolved(candidates: Iterable[RecordCandidate]) -> RecordResolution:
    return RecordResolution(
        status=ResolutionStatus.RESOLVED,
        holder_ids=_candidate_ids(candidates),
    )


def _candidate_ids(candidates: Iterable[RecordCandidate]) -> tuple[int, ...]:
    return tuple(sorted(candidate.build_id for candidate in candidates))
