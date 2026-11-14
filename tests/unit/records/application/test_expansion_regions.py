"""Stored source documents produce the same results in width and point queries."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from squid.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter
from squid.records.application.expansion_regions import DoorWidthAnalysis, analyze_door_widths
from squid.records.application.live import DoorRecordLookup, evaluate_door_record
from squid.records.application.models import CandidateBuild
from squid.records.domain import BuildKind, Candidate, ResolutionStatus
from squid.records.domain.regions import RegionAnalysisStatus, RegionLimits

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def parameter(name: str = "n", *, steps: tuple[int, ...] = (1,), initial: tuple[int, ...] = ()) -> ExpansionParameter:
    return ExpansionParameter(
        name=name, minimum=1, maximum_kind="unbounded", initial_steps=initial, repeating_steps=steps
    )


def family(
    *branches: ExpansionBranch,
    parameters: tuple[ExpansionParameter, ...] | None = None,
    build_id: int = 1,
) -> CandidateBuild:
    return CandidateBuild(
        kind=BuildKind.DOOR,
        candidate=Candidate(build_id, NOW, 1),
        version_ids=frozenset(),
        restrictions=(),
        types=(),
        expansion=DoorExpansion(parameters=parameters or (parameter(),), branches=branches or (branch(),)),
    )


def branch(
    *, width: str = "n", height: str = "3", depth: str = "1", volume: str = "10n", domain: str | None = None
) -> ExpansionBranch:
    return ExpansionBranch(
        opening_width=width, opening_height=height, opening_depth=depth, volume=volume, domain=domain
    )


def fixed(width: int, *, height: int = 3, depth: int = 1, volume: int = 1) -> CandidateBuild:
    return CandidateBuild(
        kind=BuildKind.DOOR,
        candidate=Candidate(10, NOW, volume),
        version_ids=frozenset(),
        restrictions=(),
        types=(),
        door_opening=(width, height, depth),
    )


def assert_points(
    sources: Sequence[CandidateBuild],
    result: DoorWidthAnalysis,
    *,
    height: int = 3,
    depth: int = 1,
    widths: Sequence[int] = range(1, 50),
) -> None:
    assert result.analysis.status is RegionAnalysisStatus.EXACT, result.analysis.diagnostic
    for width in widths:
        regions = [region for region in result.analysis.regions if region.domain.contains(width)]
        assert len(regions) == 1
        active = [
            source
            for source in sources
            if source.expansion is not None or source.door_opening == (width, height, depth)
        ]
        expected = evaluate_door_record(DoorRecordLookup(opening=(width, height, depth)), active, evaluated_at=NOW)
        assert regions[0].resolution == expected.resolution


def test_stored_formulas_and_fixed_exceptions() -> None:
    sources = [family(branch(volume="ceil(3n/2)+20")), fixed(9999)]
    result = analyze_door_widths(sources, height=3)
    assert_points(sources, result, widths=[1, 2, 3, 9998, 9999, 10000])
    assert [(c.build_id, c.branch_index) for c in result.configurations] == [(1, 0), (10, None)]


def test_xyz_family_becomes_a_width_slice() -> None:
    sources = [
        family(
            branch(width="x", height="y", depth="z", volume="(x+3)(y+3)(z+1)"),
            parameters=tuple(parameter(name) for name in ("x", "y", "z")),
        ),
        fixed(6, height=4, depth=2),
    ]
    assert_points(sources, analyze_door_widths(sources, height=4, depth=2), height=4, depth=2)


@pytest.mark.parametrize(
    ("width", "height", "volume"),
    [
        ("2n+1", "3", "ceil(3n/2)+10"),
        ("n", "n", "n^2+10"),
        ("ceil(n/2)", "n", "n+10"),
        ("n/2", "3", "ceil(n/3)+10"),
    ],
)
def test_module_counts_and_coupled_or_rounded_openings(width: str, height: str, volume: str) -> None:
    sources = [family(branch(width=width, height=height, volume=volume))]
    assert_points(sources, analyze_door_widths(sources, height=3))


@pytest.mark.parametrize(
    "domain",
    [
        "5 < n <= 8 or n > 15",
        "n % 2 = 0",
        "n^2 < 40",
        "not (n < 5 or n > 12)",
        "n % 2 = 0 and (n/2) % 2 = 1",
        "ceil(n/2) % 3 = 1",
        "floor(n/3) < ceil(n/5)",
    ],
)
def test_source_domain_predicates_match_point_membership(domain: str) -> None:
    sources = [family(branch(domain=domain))]
    assert_points(sources, analyze_door_widths(sources, height=3))


@pytest.mark.parametrize(("initial", "steps"), [((), (2,)), ((), (1, 2)), ((1,), (2,)), ((2, 3), (1, 3))])
def test_layer_sequences_and_finite_bounds(initial: tuple[int, ...], steps: tuple[int, ...]) -> None:
    p = parameter(initial=initial, steps=steps).model_copy(update={"maximum_kind": "finite", "maximum": 30})
    sources = [family(parameters=(p,))]
    assert_points(sources, analyze_door_widths(sources, height=3))


def test_incomplete_domain_blocks_only_queries_that_reach_unknown_sizes() -> None:
    p = parameter().model_copy(update={"maximum_kind": "unknown"})
    sources = [family(parameters=(p,))]
    result = analyze_door_widths(sources, height=3)
    assert result.analysis.status is RegionAnalysisStatus.UNSUPPORTED
    assert result.analysis.regions == ()
    assert "width 2" in (result.analysis.diagnostic or "")
    result = analyze_door_widths(sources, height=3, maximum=1)
    assert_points(sources, result, widths=[1])


def test_known_exclusion_dominates_unknown_domain() -> None:
    sources = [
        family(branch(height="5", domain="odd n"), parameters=(parameter().model_copy(update={"minimum": None}),))
    ]
    assert_points(sources, analyze_door_widths(sources, height=3))


def test_claimed_records_and_sample_completion_are_not_eligibility() -> None:
    b = branch().model_copy(update={"claimed_record_domain": "n > 100", "completion_time": "2020-01-01"})
    sources = [family(b), fixed(6, volume=60)]
    result = analyze_door_widths(sources, height=3)
    assert_points(sources, result)
    tied = next(r for r in result.analysis.regions if r.domain.contains(6))
    assert tied.resolution.status is ResolutionStatus.UNRESOLVED


def test_unknown_primary_volume_is_excluded_with_diagnostics() -> None:
    sources = [family(branch(volume="unsupported(n)")), fixed(6)]
    result = analyze_door_widths(sources, height=3)
    assert_points(sources, result)
    assert any(d.code == "unsupported" for d in result.configurations[0].diagnostics)


def test_alternative_branches_keep_their_identity() -> None:
    sources = [family(branch(volume="n+10"), branch(volume="2n+1", domain="n%2=0"))]
    result = analyze_door_widths(sources, height=3)
    assert_points(sources, result)
    assert [(c.id, c.branch_index) for c in result.configurations] == [(0, 0), (1, 1)]


@pytest.mark.parametrize(
    "source",
    [
        family(branch(width="floor(n/2)")),
        replace(family(), expansion=DoorExpansion(raw_description="unclear")),
        replace(fixed(6), door_opening=None),
    ],
)
def test_untranslatable_sources_never_disappear(source: CandidateBuild) -> None:
    result = analyze_door_widths([source], height=3)
    assert result.analysis.status is RegionAnalysisStatus.UNSUPPORTED
    assert result.analysis.regions == ()


def test_adapter_and_compiler_share_work_allowance() -> None:
    result = analyze_door_widths([family()], height=3, limits=RegionLimits(arithmetic_steps=1))
    assert result.analysis.status is RegionAnalysisStatus.LIMIT_EXCEEDED
    assert result.analysis.regions == ()


@given(st.integers(1, 5), st.integers(-3, 4), st.lists(st.integers(1, 3), min_size=1, max_size=3))
@settings(max_examples=50)
def test_generated_module_mappings_and_progressions(scale: int, offset: int, steps: list[int]) -> None:
    sources = [
        family(
            branch(width=f"{scale}n+({offset})", volume="ceil(3n/2)+10"), parameters=(parameter(steps=tuple(steps)),)
        )
    ]
    result = analyze_door_widths(sources, height=3, maximum=40)
    assert_points(sources, result, widths=range(1, 41))
