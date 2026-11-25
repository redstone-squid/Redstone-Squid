from datetime import UTC, datetime

import pytest

from squid.core.records.domain.models import (
    DOOR_TIMING_METHODS,
    EXTENDER_TIMING_METHODS,
    Candidate,
    CandidateGap,
    RecordFact,
    TimingVariant,
)
from squid.core.records.domain.resolution import (
    reduce_timing_variants,
    resolve_fastest,
    resolve_fastest_smallest,
    resolve_first,
    resolve_smallest,
    resolve_smallest_fastest,
)

EARLY = datetime(2020, 1, 1, tzinfo=UTC)
LATE = datetime(2021, 1, 1, tzinfo=UTC)


def test_smallest_ignores_missing_completion_when_volume_is_decisive() -> None:
    result = resolve_smallest(
        (
            Candidate(build_id=1, fixed_volume=20),
            Candidate(build_id=2, fixed_volume=21, completion_at=EARLY),
            Candidate(build_id=3),
        )
    )

    assert result
    assert result.holder_ids == (1,)


def test_smallest_awards_volume_tie_to_earliest_completion_and_ties_the_rest() -> None:
    result = resolve_smallest(
        (
            Candidate(build_id=1, fixed_volume=20, completion_at=LATE),
            Candidate(build_id=2, fixed_volume=20, completion_at=EARLY),
            Candidate(build_id=3, fixed_volume=21, completion_at=EARLY),
        )
    )

    assert result.holder_ids == (2,)
    assert result.tied_ids == (1,)
    assert result.gaps == ()


def test_smallest_preserves_exact_co_holders() -> None:
    result = resolve_smallest(
        (
            Candidate(build_id=2, fixed_volume=20, completion_at=EARLY),
            Candidate(build_id=1, fixed_volume=20, completion_at=EARLY),
        )
    )

    assert result
    assert result.holder_ids == (1, 2)


def test_smallest_undated_tie_loses_to_a_dated_build_but_remains_a_contender() -> None:
    result = resolve_smallest(
        (
            Candidate(build_id=1, fixed_volume=20),
            Candidate(build_id=2, fixed_volume=20, completion_at=EARLY),
        )
    )

    assert result
    assert result.holder_ids == (2,)
    assert result.tied_ids == ()
    assert result.gaps == (CandidateGap(build_id=1, field=RecordFact.COMPLETION_AT),)


def test_smallest_undated_ties_share_the_record_until_dates_are_known() -> None:
    result = resolve_smallest((Candidate(build_id=1, fixed_volume=20), Candidate(build_id=2, fixed_volume=20)))

    assert result.holder_ids == (1, 2)
    assert result.gaps == (CandidateGap(1, RecordFact.COMPLETION_AT), CandidateGap(2, RecordFact.COMPLETION_AT))


def test_smallest_has_no_candidate_without_fixed_volume() -> None:
    result = resolve_smallest((Candidate(build_id=1),))

    assert not result


def test_first_selects_the_earliest_known_completion() -> None:
    result = resolve_first(
        (
            Candidate(build_id=1, completion_at=LATE),
            Candidate(build_id=2),
            Candidate(build_id=3, completion_at=EARLY),
        )
    )

    assert result
    assert result.holder_ids == (3,)
    assert result.tied_ids == ()
    assert result.gaps == ()


def test_first_preserves_exact_co_holders() -> None:
    result = resolve_first(
        (
            Candidate(build_id=2, completion_at=EARLY),
            Candidate(build_id=1, completion_at=EARLY),
        )
    )

    assert result.holder_ids == (1, 2)


def test_first_has_no_candidate_without_completion_evidence() -> None:
    result = resolve_first((Candidate(build_id=1),))

    assert not result


def test_fastest_stops_before_missing_lower_priority_data_when_decisive() -> None:
    result = resolve_fastest(
        (
            Candidate(build_id=1, timing_variants=(TimingVariant((4, None)),)),
            Candidate(build_id=2, timing_variants=(TimingVariant((5, 1)),)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result
    assert result.holder_ids == (1,)


def test_fastest_missing_method_loses_to_a_known_one_and_is_reported() -> None:
    result = resolve_fastest(
        (
            Candidate(build_id=1, timing_variants=(TimingVariant((4, None)),)),
            Candidate(build_id=2, timing_variants=(TimingVariant((4, 2)),)),
            Candidate(build_id=3, timing_variants=(TimingVariant((5, None)),)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result
    assert result.holder_ids == (2,)
    assert result.gaps == (CandidateGap(build_id=1, field=RecordFact.OPENING_VISIBLE),)


def test_fastest_skips_a_method_nobody_measured_and_reports_it() -> None:
    result = resolve_fastest(
        (
            Candidate(build_id=1, timing_variants=(TimingVariant((4, None, 6)),)),
            Candidate(build_id=2, timing_variants=(TimingVariant((4, None, 5)),)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result.holder_ids == (2,)
    assert result.gaps == (CandidateGap(1, RecordFact.OPENING_VISIBLE), CandidateGap(2, RecordFact.OPENING_VISIBLE))


def test_fastest_supports_negative_reset_times() -> None:
    result = resolve_fastest(
        (
            Candidate(build_id=1, timing_variants=(TimingVariant((4, 3, 5, 3, -1, 0)),)),
            Candidate(build_id=2, timing_variants=(TimingVariant((4, 3, 5, 3, 0, -2)),)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result.holder_ids == (1,)


def test_fastest_uses_extender_method_order() -> None:
    result = resolve_fastest(
        (
            Candidate(build_id=1, timing_variants=(TimingVariant((4, 5, 0, 0)),)),
            Candidate(build_id=2, timing_variants=(TimingVariant((5, 1, 0, 0)),)),
        ),
        EXTENDER_TIMING_METHODS,
    )

    assert result.holder_ids == (1,)


def test_fastest_uses_completion_and_co_holders_after_all_methods_tie() -> None:
    timing = TimingVariant((4, 3, 5, 4, 0, 0))
    result = resolve_fastest(
        (
            Candidate(build_id=3, completion_at=LATE, timing_variants=(timing,)),
            Candidate(build_id=2, completion_at=EARLY, timing_variants=(timing,)),
            Candidate(build_id=1, completion_at=EARLY, timing_variants=(timing,)),
        ),
        DOOR_TIMING_METHODS,
    )

    assert result
    assert result.holder_ids == (1, 2)
    assert result.tied_ids == (3,)


def test_fastest_smallest_uses_speed_within_the_minimum_volume_pool() -> None:
    result = resolve_fastest_smallest(
        (
            Candidate(build_id=1, fixed_volume=10, timing_variants=(TimingVariant((5,)),)),
            Candidate(build_id=2, fixed_volume=10, timing_variants=(TimingVariant((4,)),)),
            Candidate(build_id=3, fixed_volume=11, timing_variants=(TimingVariant((3,)),)),
        ),
        ("opening",),
    )

    assert result.holder_ids == (2,)


def test_smallest_fastest_uses_volume_within_the_fastest_timing_pool() -> None:
    result = resolve_smallest_fastest(
        (
            Candidate(build_id=1, fixed_volume=11, timing_variants=(TimingVariant((4,)),)),
            Candidate(build_id=2, fixed_volume=10, timing_variants=(TimingVariant((4,)),)),
            Candidate(build_id=3, fixed_volume=9, timing_variants=(TimingVariant((5,)),)),
        ),
        ("opening",),
    )

    assert result.holder_ids == (2,)


def test_smallest_fastest_reports_a_build_that_might_join_the_fastest_pool() -> None:
    result = resolve_smallest_fastest(
        (
            Candidate(build_id=1, fixed_volume=10, timing_variants=(TimingVariant((4, None)),)),
            Candidate(build_id=2, fixed_volume=11, timing_variants=(TimingVariant((4, 2)),)),
        ),
        ("opening", RecordFact.OPENING_VISIBLE),
    )

    assert result.holder_ids == (2,)
    assert result.gaps == (CandidateGap(build_id=1, field=RecordFact.OPENING_VISIBLE),)


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
        Candidate(build_id=1, fixed_volume=0)
