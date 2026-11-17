"""Concrete family configurations compete without merging alternative metrics."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from squid.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter, ExpansionTiming
from squid.builds.domain.expansion_evaluation import PointStatus
from squid.records.application.live import DoorRecordLookup, LiveDoorRecord, evaluate_door_record
from squid.records.application.models import CandidateBuild
from squid.records.domain import BaseTitle, BuildKind, Candidate, ResolutionStatus, TimingVariant
from squid.records.domain.door_evidence import DoorRecordEvidence

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def fixed(build_id: int = 1, *, volume: int = 100, opening_time: int = 10) -> CandidateBuild:
    return CandidateBuild(
        kind=BuildKind.DOOR,
        candidate=Candidate(build_id, NOW, volume, (TimingVariant((opening_time, 0, 0, 0, 0, 0)),)),
        version_ids=frozenset(),
        restrictions=(),
        types=(),
    )


def branch(volume: str = "10n", opening_time: str = "n") -> ExpansionBranch:
    return ExpansionBranch(
        opening_width="n",
        opening_height="3",
        opening_depth="1",
        volume=volume,
        timings=tuple(
            ExpansionTiming(method=method, expression=opening_time if method == "opening" else "0", unit="game_tick")
            for method in ("opening", "opening_visible", "closing", "closing_visible", "opening_reset", "closing_reset")
        ),
    )


def family(*branches: ExpansionBranch, parameter: ExpansionParameter | None = None) -> CandidateBuild:
    return replace(
        fixed(2, volume=1, opening_time=1),
        expansion=DoorExpansion(
            parameters=(
                parameter
                or ExpansionParameter(
                    name="n",
                    minimum=1,
                    maximum_kind="unbounded",
                    initial_steps=(),
                    repeating_steps=(1,),
                ),
            ),
            branches=branches or (branch(),),
        ),
    )


def lookup(*sources: CandidateBuild, title: BaseTitle = BaseTitle.SMALLEST, width: int = 6) -> LiveDoorRecord:
    return evaluate_door_record(DoorRecordLookup(opening=(width, 3, 1), base_title=title), sources, evaluated_at=NOW)


@pytest.mark.parametrize("title", [BaseTitle.SMALLEST, BaseTitle.FASTEST])
def test_family_competes_in_fixed_size_category(title: BaseTitle) -> None:
    result = lookup(fixed(), family(), title=title)
    assert result.resolution.holder_ids == (2,)
    assert result.holder_configuration_ids == (1,)
    configuration = result.configurations[1]
    assert configuration.parameters == (("n", 6),)
    assert configuration.volume == 60
    assert configuration.timing_variants[0][0] == 6
    assert configuration.completion_at is None


def test_family_does_not_borrow_sample_measurements_or_completion() -> None:
    source = family(branch(volume="1000", opening_time="100"))
    assert lookup(fixed(), source).resolution.holder_ids == (1,)
    assert lookup(fixed(), source, title=BaseTitle.FASTEST).resolution.holder_ids == (1,)
    result = lookup(source, title=BaseTitle.FIRST)
    assert result.resolution.status is ResolutionStatus.NO_CANDIDATE
    assert any(d.code == "unverified_completion" for d in result.configurations[0].diagnostics)


def test_completion_tie_stays_unresolved_across_builds() -> None:
    result = lookup(fixed(volume=60), family())
    assert result.resolution.status is ResolutionStatus.UNRESOLVED
    assert result.resolution.provisional_holder_ids == (1, 2)
    assert [(gap.build_id, gap.field) for gap in result.resolution.gaps] == [(2, "completion_at")]


@pytest.mark.parametrize("title", [BaseTitle.FASTEST_SMALLEST, BaseTitle.SMALLEST_FASTEST])
def test_alternatives_do_not_mix_volume_and_speed(title: BaseTitle) -> None:
    source = family(branch("10", "10"), branch("100", "1"))
    competitor = (
        fixed(volume=10, opening_time=5) if title is BaseTitle.FASTEST_SMALLEST else fixed(volume=20, opening_time=1)
    )
    result = lookup(competitor, source, title=title)
    assert result.resolution.holder_ids == (1,)
    assert len(result.configurations) == 3


def test_same_build_alternatives_do_not_require_completion_to_identify_holder() -> None:
    result = lookup(family(branch("10", "10"), branch("10", "1")))
    assert result.resolution.holder_ids == (2,)
    assert result.holder_configuration_ids == ()
    assert result.provisional_configuration_ids == (0, 1)


@pytest.mark.parametrize("title", list(BaseTitle))
def test_unknown_membership_does_not_block_official_holder(title: BaseTitle) -> None:
    source = family(parameter=ExpansionParameter(name="n", minimum=1, initial_steps=(), repeating_steps=(1,)))
    result = lookup(fixed(), source, title=title)
    assert result.resolution.status is ResolutionStatus.RESOLVED
    assert result.resolution.holder_ids == (1,)
    assert result.resolution.provisional_holder_ids == ()
    assert result.resolution.gaps == ()
    configuration = result.configurations[1]
    assert configuration.membership is PointStatus.UNRESOLVED
    assert configuration.diagnostics


def test_unknown_membership_alone_cannot_claim_record() -> None:
    source = family(parameter=ExpansionParameter(name="n", minimum=1, initial_steps=(), repeating_steps=(1,)))
    result = lookup(source)
    assert result.resolution.status is ResolutionStatus.NO_CANDIDATE
    assert result.resolution.holder_ids == ()
    assert result.resolution.provisional_holder_ids == ()
    assert result.configurations[0].membership is PointStatus.UNRESOLVED


def test_document_without_branches_is_reported_but_does_not_block() -> None:
    # Ingestion keeps partial documents. One that names no opening size must not
    # unresolve every record in its category.
    source = replace(family(), expansion=DoorExpansion(raw_description="unclear"))
    result = lookup(fixed(), source)
    assert result.resolution.status is ResolutionStatus.RESOLVED
    assert result.resolution.holder_ids == (1,)
    assert result.configurations[1].membership is PointStatus.UNMAPPED
    assert result.configurations[1].diagnostics[0].code == "missing"


def test_periodic_exclusions_do_not_block_fixed_door() -> None:
    source = family(
        parameter=ExpansionParameter(
            name="n",
            minimum=1,
            maximum_kind="unbounded",
            initial_steps=(),
            repeating_steps=(2,),
        )
    )
    result = lookup(fixed(), source)
    assert result.resolution.holder_ids == (1,)
    assert result.configurations[1].membership is PointStatus.OUTSIDE_DOMAIN
    assert lookup(source, width=7).resolution.holder_ids == (2,)


def test_floor_ceiling_and_large_openings_evaluate_directly() -> None:
    result = lookup(family(branch("10ceil(n/2)", "floor(n/2)")), width=9999)
    assert result.resolution.holder_ids == (2,)
    assert result.configurations[0].volume == 50000
    assert result.configurations[0].timing_variants[0][0] == 4999


def test_unknown_primary_metric_preserves_existing_exclusion_rule() -> None:
    result = lookup(fixed(), family(branch(volume="unsupported(n)")))
    assert result.resolution.holder_ids == (1,)
    assert result.configurations[1].volume is None
    assert result.configurations[1].diagnostics


def test_each_branch_reduces_its_own_slowest_timing_variant() -> None:
    source_branch = branch()
    source_branch = source_branch.model_copy(
        update={
            "timings": (
                ExpansionTiming(method="opening", expression="1", unit="game_tick", variant="best"),
                ExpansionTiming(method="opening", expression="20", unit="game_tick", variant="worst"),
            ),
        }
    )
    assert lookup(fixed(), family(source_branch), title=BaseTitle.FASTEST).resolution.holder_ids == (1,)


@pytest.mark.parametrize("opening", [(0, 3, 1), (-1, 3, 1), (True, 3, 1), (1.5, 3, 1), (2**31, 3, 1), (3, 1)])
def test_request_rejects_invalid_opening(opening: tuple[object, ...]) -> None:
    with pytest.raises(ValidationError):
        DoorRecordLookup.model_validate({"opening": opening})


@pytest.mark.parametrize("values", [((7, "exact", "3"),), ((1, "exact", "3"), (1, "exact", "4"))])
def test_request_rejects_orphan_or_duplicate_thresholds(values: tuple[tuple[int, str, str], ...]) -> None:
    with pytest.raises(ValidationError):
        DoorRecordLookup.model_validate({"opening": (6, 3, 1), "restriction_ids": [1], "restriction_values": values})


def test_point_evidence_versions_preserve_old_publications() -> None:
    evidence = DoorRecordEvidence(evaluation=lookup(fixed()), sources=())
    assert evidence.calculator_version == "door-point-2"
    previous = evidence.model_dump(mode="json")
    previous["calculator_version"] = "door-point-1"
    assert DoorRecordEvidence.model_validate(previous).calculator_version == "door-point-1"
