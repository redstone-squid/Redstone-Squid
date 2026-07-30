from datetime import UTC, datetime

import pytest

from squid.records.domain.models import (
    DOOR_TIMING_METHODS,
    EXTENDER_TIMING_METHODS,
    CandidateGap,
    RecordCandidate,
    ResolutionStatus,
    TimingVariant,
)
from squid.records.domain.resolution import reduce_timing_variants, resolve_fastest, resolve_smallest

EARLY = datetime(2020, 1, 1, tzinfo=UTC)
LATE = datetime(2021, 1, 1, tzinfo=UTC)


def test_smallest_ignores_missing_completion_when_volume_is_decisive() -> None:
    result = resolve_smallest(
        (
            RecordCandidate(build_id=1, fixed_volume=20),
            RecordCandidate(build_id=2, fixed_volume=21, completion_at=EARLY),
            RecordCandidate(build_id=3),
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.holder_ids == (1,)


def test_smallest_uses_earliest_completion_for_volume_tie() -> None:
    result = resolve_smallest(
        (
            RecordCandidate(build_id=1, fixed_volume=20, completion_at=LATE),
            RecordCandidate(build_id=2, fixed_volume=20, completion_at=EARLY),
        )
    )

    assert result.holder_ids == (2,)


def test_smallest_preserves_exact_co_holders() -> None:
    result = resolve_smallest(
        (
            RecordCandidate(build_id=2, fixed_volume=20, completion_at=EARLY),
            RecordCandidate(build_id=1, fixed_volume=20, completion_at=EARLY),
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.holder_ids == (1, 2)


def test_smallest_is_unresolved_when_tied_build_lacks_completion() -> None:
    result = resolve_smallest(
        (
            RecordCandidate(build_id=1, fixed_volume=20),
            RecordCandidate(build_id=2, fixed_volume=20, completion_at=EARLY),
        )
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.holder_ids == ()
    assert result.provisional_holder_ids == (1, 2)
    assert result.gaps == (CandidateGap(build_id=1, field="completion_at"),)


def test_smallest_has_no_candidate_without_fixed_volume() -> None:
    result = resolve_smallest((RecordCandidate(build_id=1),))

    assert result.status is ResolutionStatus.NO_CANDIDATE


def test_fastest_stops_before_missing_lower_priority_data_when_decisive() -> None:
    result = resolve_fastest(
        (
            RecordCandidate(build_id=1, timing_variants=(TimingVariant((4, None)),)),
            RecordCandidate(build_id=2, timing_variants=(TimingVariant((5, 1)),)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.holder_ids == (1,)


def test_fastest_reports_only_decisive_missing_method() -> None:
    result = resolve_fastest(
        (
            RecordCandidate(build_id=1, timing_variants=(TimingVariant((4, None)),)),
            RecordCandidate(build_id=2, timing_variants=(TimingVariant((4, 2)),)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.gaps == (CandidateGap(build_id=1, field="opening_visible"),)


def test_fastest_supports_negative_reset_times() -> None:
    result = resolve_fastest(
        (
            RecordCandidate(build_id=1, timing_variants=(TimingVariant((4, 3, 5, 3, -1, 0)),)),
            RecordCandidate(build_id=2, timing_variants=(TimingVariant((4, 3, 5, 3, 0, -2)),)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result.holder_ids == (1,)


def test_fastest_uses_extender_method_order() -> None:
    result = resolve_fastest(
        (
            RecordCandidate(build_id=1, timing_variants=(TimingVariant((4, 5, 0, 0)),)),
            RecordCandidate(build_id=2, timing_variants=(TimingVariant((5, 1, 0, 0)),)),
        ),
        EXTENDER_TIMING_METHODS,
    )

    assert result.holder_ids == (1,)


def test_fastest_uses_completion_and_co_holders_after_all_methods_tie() -> None:
    timing = TimingVariant((4, 3, 5, 4, 0, 0))
    result = resolve_fastest(
        (
            RecordCandidate(build_id=3, completion_at=LATE, timing_variants=(timing,)),
            RecordCandidate(build_id=2, completion_at=EARLY, timing_variants=(timing,)),
            RecordCandidate(build_id=1, completion_at=EARLY, timing_variants=(timing,)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.holder_ids == (1, 2)


def test_reduce_timing_variants_selects_lexicographically_slowest_behavior() -> None:
    reduction = reduce_timing_variants(
        (
            TimingVariant((4, 8, 1)),
            TimingVariant((5, 2, 1)),
            TimingVariant((5, 3, 0)),
        )
    )

    assert reduction.timing == TimingVariant((5, 3, 0))
    assert reduction.missing_index is None


def test_reduce_timing_variants_reports_missing_tied_secondary_method() -> None:
    reduction = reduce_timing_variants((TimingVariant((5, None, 2)), TimingVariant((5, 3, 1))))

    assert reduction.timing == TimingVariant((5, None, None))
    assert reduction.missing_index == 1


def test_record_candidate_rejects_non_positive_volume() -> None:
    with pytest.raises(ValueError, match="positive"):
        RecordCandidate(build_id=1, fixed_volume=0)
