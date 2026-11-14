from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter, ExpansionTiming
from squid.builds.domain.expansion_evaluation import PointStatus, evaluate_expansion, normalize_expansion
from squid.builds.domain.expansion_math import ExpressionError
from squid.records.domain import Candidate, TimingVariant, resolve_fastest, resolve_smallest


def parameter(name: str = "n", /, **changes: object) -> ExpansionParameter:
    return ExpansionParameter.model_validate(
        {
            "name": name,
            "minimum": 1,
            "maximum_kind": "unbounded",
            "initial_steps": [],
            "repeating_steps": [1],
            **changes,
        }
    )


def family(parameters: tuple[ExpansionParameter, ...] | None = None, /, **changes: object) -> DoorExpansion:
    branch = ExpansionBranch.model_validate(
        {"opening_width": "n", "opening_height": "3", "opening_depth": "1", "volume": "40+20n", **changes}
    )
    return DoorExpansion(parameters=parameters if parameters is not None else (parameter(),), branches=(branch,))


def test_multidirectional_configuration_retains_source_and_ignores_record_claims() -> None:
    source = family(
        tuple(parameter(name) for name in ("x", "y", "z")),
        opening_width="x",
        opening_height="y",
        opening_depth="z",
        volume="(x+3)(y+3)(z+1)",
        claimed_record_domain="x > 100",
    )
    normalized = normalize_expansion(source)
    result = evaluate_expansion(normalized, (6, 3, 2)).configurations[0]
    assert result.status is PointStatus.SUPPORTED
    assert result.volume == 162
    assert result.parameters == (("x", 6), ("y", 3), ("z", 2))
    assert normalized.source is source
    assert source.branches[0].volume == "(x+3)(y+3)(z+1)"


@pytest.mark.parametrize(
    ("initial", "steps", "supported"),
    [
        ((), (2,), [2, 4, 6, 8]),
        ((), (1, 2), [2, 3, 5, 6, 8, 9]),
        ((1,), (2,), [2, 3, 5, 7, 9]),
    ],
)
def test_finite_periodic_domains(initial: tuple[int, ...], steps: tuple[int, ...], supported: list[int]) -> None:
    source = family(
        (parameter(minimum=2, maximum_kind="finite", maximum=9, initial_steps=initial, repeating_steps=steps),)
    )
    normalized = normalize_expansion(source)
    assert [
        n
        for n in range(1, 12)
        if evaluate_expansion(normalized, (n, 3, 1)).configurations[0].status is PointStatus.SUPPORTED
    ] == supported


@given(st.integers(1, 10**30))
def test_module_counts_are_recovered_without_enumeration(n: int) -> None:
    source = family(opening_width="2*n+1", opening_height="n+2", volume="(2*n+4)*(n+5)")
    result = evaluate_expansion(normalize_expansion(source), (2 * n + 1, n + 2, 1)).configurations[0]
    assert result.status is PointStatus.SUPPORTED
    assert result.volume == (2 * n + 4) * (n + 5)
    assert result.parameters == (("n", n),)


def test_coupled_mapping_and_noninteger_parameters() -> None:
    source = family((parameter("x"), parameter("y")), opening_width="x+y", opening_height="x-y", volume="x*y")
    normalized = normalize_expansion(source)
    result = evaluate_expansion(normalized, (8, 2, 1)).configurations[0]
    assert result.parameters == (("x", 5), ("y", 3))
    assert result.volume == 15
    assert evaluate_expansion(normalized, (7, 2, 1)).configurations[0].status is PointStatus.OUTSIDE_DOMAIN
    square = normalize_expansion(family(opening_height="n"))
    assert evaluate_expansion(square, (6, 5, 1)).configurations[0].status is PointStatus.OUTSIDE_DOMAIN


@pytest.mark.parametrize(
    "changes", [{"maximum_kind": "unknown"}, {"minimum": None}, {"initial_steps": None}, {"repeating_steps": None}]
)
def test_unknown_domain_facts_do_not_become_defaults(changes: dict[str, object]) -> None:
    normalized = normalize_expansion(family((parameter(**changes),)))
    result = evaluate_expansion(normalized, (6, 3, 1)).configurations[0]
    assert result.status is PointStatus.UNRESOLVED
    assert any(d.code == "incomplete_domain" for d in result.diagnostics)
    assert result.volume is None


def test_known_exclusions_and_start_survive_incomplete_domain() -> None:
    normalized = normalize_expansion(family((ExpansionParameter(name="n", minimum=3),)))
    assert evaluate_expansion(normalized, (2, 3, 1)).configurations[0].status is PointStatus.OUTSIDE_DOMAIN
    assert evaluate_expansion(normalized, (3, 3, 1)).configurations[0].status is PointStatus.SUPPORTED
    guarded = normalize_expansion(family((ExpansionParameter(name="n"),), domain="n < 4"))
    assert evaluate_expansion(guarded, (6, 3, 1)).configurations[0].status is PointStatus.OUTSIDE_DOMAIN


def test_branches_keep_their_own_metrics_and_phase_guards() -> None:
    source = family(domain="n % 2 = 0")
    alternate = ExpansionBranch(
        opening_width="n", opening_height="3", opening_depth="1", volume="60+10n", domain="n % 2 = 1"
    )
    normalized = normalize_expansion(
        DoorExpansion(parameters=source.parameters, branches=(*source.branches, alternate))
    )
    results = evaluate_expansion(normalized, (7, 3, 1)).configurations
    assert results[0].status is PointStatus.OUTSIDE_DOMAIN
    assert results[1].volume == 130
    overlapping = normalize_expansion(
        DoorExpansion(
            parameters=source.parameters, branches=(source.branches[0], alternate.model_copy(update={"domain": None}))
        )
    )
    assert [result.volume for result in evaluate_expansion(overlapping, (6, 3, 1)).configurations] == [160, 120]


@pytest.mark.parametrize("opening", [{"opening_width": "n*n"}, {"opening_depth": None}, {"opening_width": "3"}])
def test_unsupported_or_ambiguous_mapping_is_reported(opening: dict[str, object]) -> None:
    result = evaluate_expansion(normalize_expansion(family(**opening)), (6, 3, 1)).configurations[0]
    assert result.status is PointStatus.UNRESOLVED
    assert any(d.code == "ambiguous_mapping" for d in result.diagnostics)


def test_missing_branches_and_unsupported_names_have_diagnostics() -> None:
    assert normalize_expansion(DoorExpansion()).diagnostics[0].path == "branches"
    result = normalize_expansion(family((ExpansionParameter(name="not"),)))
    assert result.diagnostics[0].path == "parameters"


@pytest.mark.parametrize(("field", "value"), [("domain", "odd n"), ("volume", "ceil(n/2)"), ("volume", None)])
def test_unsupported_claim_does_not_disappear(field: str, value: str | None) -> None:
    source = family(**{field: value})
    result = evaluate_expansion(normalize_expansion(source), (6, 3, 1)).configurations[0]
    assert result.status is (PointStatus.UNRESOLVED if field == "domain" else PointStatus.SUPPORTED)
    assert result.volume is None
    assert any(d.path == f"branches[0].{field}" for d in result.diagnostics)
    assert getattr(source.branches[0], field) == value


@pytest.mark.parametrize("volume", ["n/4", "n-6", "-n"])
def test_noninteger_or_nonpositive_volume_is_not_a_record_measurement(volume: str) -> None:
    result = evaluate_expansion(normalize_expansion(family(volume=volume)), (6, 3, 1)).configurations[0]
    assert result.status is PointStatus.SUPPORTED
    assert result.volume is None
    assert result.diagnostics[-1].code == "invalid_value"


def test_timing_units_variants_unknowns_and_reset_values() -> None:
    timings = (
        ExpansionTiming(method="opening", expression="0.05n", unit="second", variant="a"),
        ExpansionTiming(method="closing", expression="2", unit="redstone_tick", variant="a"),
        ExpansionTiming(method="opening_reset", expression="-2", unit="game_tick", variant="a"),
        ExpansionTiming(method="opening_visible", expression="n", variant="a"),
        ExpansionTiming(method="opening", expression="n+1", unit="game_tick", variant="b"),
    )
    result = evaluate_expansion(normalize_expansion(family(timings=timings)), (6, 3, 1)).configurations[0]
    assert result.timing_variants == ((6, None, 4, None, -2, None), (7, None, None, None, None, None))
    assert any(d.path.endswith(".unit") for d in result.diagnostics)


def test_invalid_and_conflicting_timings_stay_unknown() -> None:
    timings = tuple(ExpansionTiming(method="opening", expression=str(value), unit="game_tick") for value in (2, 3, 4))
    result = evaluate_expansion(normalize_expansion(family(timings=timings)), (6, 3, 1)).configurations[0]
    assert result.timing_variants[0][0] is None
    assert any(d.code == "duplicate" for d in result.diagnostics)
    result = evaluate_expansion(
        normalize_expansion(
            family(
                timings=(
                    ExpansionTiming(method="opening", expression="-1", unit="game_tick"),
                    ExpansionTiming(method="closing", expression="0.01", unit="second"),
                )
            )
        ),
        (6, 3, 1),
    ).configurations[0]
    assert result.timing_variants == ((None,) * 6,)
    assert sum(d.code == "invalid_value" for d in result.diagnostics) == 2


def test_point_metrics_compete_with_fixed_doors_using_existing_rules() -> None:
    source = family(timings=(ExpansionTiming(method="opening", expression="n", unit="game_tick"),))
    configuration = evaluate_expansion(normalize_expansion(source), (6, 3, 1)).configurations[0]
    expanded = Candidate(
        build_id=1,
        completion_at=datetime(2020, 1, 1, tzinfo=UTC),
        fixed_volume=configuration.volume,
        timing_variants=tuple(TimingVariant(values) for values in configuration.timing_variants),
    )
    fixed = Candidate(build_id=2, fixed_volume=170, timing_variants=(TimingVariant((5,)),))
    assert resolve_smallest((expanded, fixed)).holder_ids == (1,)
    assert resolve_fastest((expanded, fixed), ("opening",)).holder_ids == (2,)


def test_bad_point_is_rejected() -> None:
    normalized = normalize_expansion(family())
    for point in [(0, 3, 1), (True, 3, 1), (2**256, 3, 1)]:
        with pytest.raises(ExpressionError):
            evaluate_expansion(normalized, point)


@given(st.lists(st.integers(1, 5), max_size=5), st.lists(st.integers(1, 5), min_size=1, max_size=5))
def test_periodic_membership_matches_explicit_small_sequence(initial: list[int], steps: list[int]) -> None:
    supported = {1}
    size = 1
    for step in initial:
        size += step
        supported.add(size)
    repeat = 0
    while size < 80:
        size += steps[repeat % len(steps)]
        repeat += 1
        supported.add(size)
    normalized = normalize_expansion(family((parameter(initial_steps=initial, repeating_steps=steps),)))
    for n in range(1, 80):
        result = evaluate_expansion(normalized, (n, 3, 1)).configurations[0]
        assert (result.status is PointStatus.SUPPORTED) == (n in supported)
