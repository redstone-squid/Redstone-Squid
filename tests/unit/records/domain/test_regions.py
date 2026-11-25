"""Region membership must agree with the concrete smallest-record resolver."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from squid.core.builds.domain.expansion_math import ExpressionParser
from squid.core.records.domain.models import Candidate
from squid.core.records.domain.regions import (
    IntegerProgression,
    RegionAnalysisStatus,
    RegionLimits,
    SmallestRegionCandidate,
    smallest_record_regions,
)
from squid.core.records.domain.resolution import resolve_smallest

PARSER = ExpressionParser(("n",))


def candidate(
    id: int, expression: str | None, *domains: IntegerProgression, completion_at: datetime | None = None
) -> SmallestRegionCandidate:
    return SmallestRegionCandidate(
        id,
        id,
        domains or (IntegerProgression(),),
        PARSER.formula(expression) if expression is not None else None,
        completion_at,
    )


def test_disconnected_unbounded_winning_ranges() -> None:
    result = smallest_record_regions(
        [
            candidate(1, "n^3+1000"),
            candidate(2, "n^3+1000+(n-5)(n-9)(n-15)"),
        ]
    )
    assert result.status is RegionAnalysisStatus.EXACT
    assert [r.domain for r in result.regions if r.resolution.holder_ids == (1,)] == [
        IntegerProgression(6, 8),
        IntegerProgression(16),
    ]
    assert [r.domain for r in result.regions if r.resolution.holder_ids == (1, 2)] == [
        IntegerProgression(5, 5),
        IntegerProgression(9, 9),
        IntegerProgression(15, 15),
    ]


def test_fixed_build_creates_single_size_exception() -> None:
    result = smallest_record_regions([candidate(1, "10n"), candidate(2, "1", IntegerProgression(9999, 9999))])
    assert result.status is RegionAnalysisStatus.EXACT
    assert [(r.domain, r.resolution.holder_ids) for r in result.regions] == [
        (IntegerProgression(1, 9998), (1,)),
        (IntegerProgression(9999, 9999), (2,)),
        (IntegerProgression(10000), (1,)),
    ]


def test_periodic_holes_initial_exception_and_finite_limits() -> None:
    domains = (IntegerProgression(1, 1), IntegerProgression(2, 8, 2, 0))
    result = smallest_record_regions([candidate(1, "n", *domains)], maximum=12)
    assert result.status is RegionAnalysisStatus.EXACT
    for n in range(1, 13):
        region = next(r for r in result.regions if r.domain.contains(n))
        assert region.resolution.holder_ids == ((1,) if n in (1, 2, 4, 6, 8) else ())
    for region in result.regions:
        assert region.domain.contains(region.domain.minimum)


def test_floor_ceiling_crossover_includes_ties_and_witnesses() -> None:
    candidates = [candidate(1, "10ceil(n/2)"), candidate(2, "6n")]
    result = smallest_record_regions(candidates)
    assert result.status is RegionAnalysisStatus.EXACT
    for n in (*range(1, 100), 10**12):
        regions = [r for r in result.regions if r.domain.contains(n)]
        assert len(regions) == 1
        expected = resolve_smallest(
            [
                Candidate(c.configuration_id, fixed_volume=int(c.volume.evaluate((n,))))
                for c in candidates
                if c.volume is not None
            ]
        )
        assert regions[0].resolution == expected


def test_unknown_nonintegral_and_nonpositive_volumes_are_excluded() -> None:
    result = smallest_record_regions([candidate(1, "n/2-2"), candidate(2, None)], maximum=10)
    assert result.status is RegionAnalysisStatus.EXACT
    for n in range(1, 11):
        region = next(r for r in result.regions if r.domain.contains(n))
        assert region.resolution.holder_ids == ((1,) if n in (6, 8, 10) else ())


def test_completion_ties_and_same_build_alternatives() -> None:
    earlier = datetime(2020, 1, 1, tzinfo=UTC)
    later = datetime(2021, 1, 1, tzinfo=UTC)
    sources = [candidate(1, "n", completion_at=earlier), candidate(2, "n", completion_at=later)]
    assert smallest_record_regions(sources).regions[0].resolution.holder_ids == (1,)
    sources[1] = replace(sources[1], completion_at=earlier)
    assert smallest_record_regions(sources).regions[0].resolution.holder_ids == (1, 2)
    sources = [candidate(1, "n"), replace(candidate(2, "n"), build_id=1)]
    result = smallest_record_regions(sources)
    assert result.regions[0].resolution.holder_ids == (1,)
    assert result.regions[0].resolution.gaps == ()
    assert result.regions[0].holder_configuration_ids == (1, 2)


def test_unknown_domain_is_not_a_no_candidate_claim() -> None:
    result = smallest_record_regions([candidate(1, "n"), replace(candidate(2, "1"), membership_known=False)])
    assert result.status is RegionAnalysisStatus.UNSUPPORTED
    assert result.regions == ()
    assert result.diagnostic is not None


@pytest.mark.parametrize(
    "limits",
    [
        RegionLimits(candidates=1),
        RegionLimits(period=1),
        RegionLimits(regions=1),
        RegionLimits(arithmetic_steps=1),
    ],
)
def test_resource_limits_do_not_return_partial_winners(limits: RegionLimits) -> None:
    result = smallest_record_regions([candidate(1, "ceil(n/2)"), candidate(2, "5")], limits=limits)
    assert result.status is RegionAnalysisStatus.LIMIT_EXCEEDED
    assert result.regions == ()
    assert result.diagnostic is not None


def test_variables_must_share_one_coordinate() -> None:
    different = replace(candidate(2, "n"), volume=ExpressionParser(("x",)).polynomial("x"))
    assert smallest_record_regions([candidate(1, "n"), different]).status is RegionAnalysisStatus.UNSUPPORTED
    multiple = replace(candidate(2, "n"), volume=ExpressionParser(("x", "y")).polynomial("x*y"))
    assert smallest_record_regions([multiple]).status is RegionAnalysisStatus.UNSUPPORTED


def test_empty_population_and_query_clipping() -> None:
    result = smallest_record_regions([], minimum=7, maximum=20)
    assert result.status is RegionAnalysisStatus.EXACT
    assert result.regions[0].domain == IntegerProgression(7, 20)
    assert not result.regions[0].resolution
    clipped = smallest_record_regions([candidate(1, "ceil(n/2)")], minimum=7, maximum=7)
    assert len(clipped.regions) == 1
    assert clipped.regions[0].domain.minimum == 7
    assert clipped.regions[0].domain.maximum == 7


@given(
    st.lists(
        st.tuples(
            st.integers(-4, 4),
            st.integers(-5, 5),
            st.integers(-8, 8),
            st.integers(1, 4),
            st.integers(1, 10),
        ),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=100)
def test_generated_regions_match_every_concrete_point(facts: list[tuple[int, int, int, int, int]]) -> None:
    sources = [
        candidate(i + 1, f"{a}n^2+{b}n+{c}", IntegerProgression(start, 50, period, start % period))
        for i, (a, b, c, period, start) in enumerate(facts)
    ]
    result = smallest_record_regions(sources, maximum=60)
    assert result.status is RegionAnalysisStatus.EXACT
    for n in range(1, 61):
        active = []
        for source in sources:
            assert source.volume is not None
            volume = source.volume.evaluate((n,))
            if volume > 0 and volume.denominator == 1 and any(domain.contains(n) for domain in source.domain):
                active.append(Candidate(source.configuration_id, fixed_volume=volume.numerator))
        expected = resolve_smallest(active)
        matching = [r for r in result.regions if r.domain.contains(n)]
        assert len(matching) == 1
        assert matching[0].resolution == expected


def test_huge_winning_boundary_does_not_materialize_sizes() -> None:
    root = 10**60
    result = smallest_record_regions([candidate(1, "n"), candidate(2, str(root))])
    assert result.status is RegionAnalysisStatus.EXACT
    assert [(r.domain.minimum, r.domain.maximum, bool(r.resolution)) for r in result.regions] == [
        (1, root - 1, True),
        (root, root, True),
        (root + 1, None, True),
    ]
    assert result.regions[0].resolution.holder_ids == (1,)
    assert result.regions[2].resolution.holder_ids == (2,)


def test_overlapping_progressions_and_repeating_one_two_steps() -> None:
    result = smallest_record_regions(
        [
            candidate(
                1,
                "10n",
                IntegerProgression(1, None, 3, 1),
                IntegerProgression(2, None, 3, 2),
                IntegerProgression(1, 2),
            )
        ],
        maximum=20,
    )
    assert result.status is RegionAnalysisStatus.EXACT
    for n in range(1, 21):
        regions = [r for r in result.regions if r.domain.contains(n)]
        assert len(regions) == 1
        assert regions[0].resolution.holder_ids == ((1,) if n % 3 else ())


@pytest.mark.parametrize(
    "sources",
    [
        [candidate(1, "n", IntegerProgression(1, None, 11, 0)), candidate(2, "n", IntegerProgression(1, None, 13, 0))],
        [candidate(1, "n", *(IntegerProgression(i, i) for i in range(1, 130)))],
        [candidate(1, "n", IntegerProgression(2**257))],
    ],
)
def test_domain_complexity_limits_are_explicit(sources: list[SmallestRegionCandidate]) -> None:
    result = smallest_record_regions(sources)
    assert result.status is RegionAnalysisStatus.LIMIT_EXCEEDED
    assert result.regions == ()
