"""Read-only comparisons at a concrete opening size.

Families contribute separate branch configurations. Resolver IDs are local to a
lookup so alternatives cannot overwrite each other or combine their metrics.
The public result maps those IDs back to builds and retains branch evidence.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from squid.builds.domain import DoorOrientationLiteral
from squid.builds.domain.expansion_evaluation import (
    ExpansionDiagnostic,
    PointStatus,
    evaluate_expansion,
    normalize_expansion,
)
from squid.records.application.door_evidence import LiveDoorConfiguration, LiveDoorRecord
from squid.records.application.models import CandidateBuild, CategoryIdentity
from squid.records.domain import (
    BaseTitle,
    BuildKind,
    Candidate,
    ResolutionStatus,
    TimingVariant,
    TitleResolution,
    resolve_fastest,
    resolve_fastest_smallest,
    resolve_first,
    resolve_smallest,
    resolve_smallest_fastest,
)
from squid.records.domain.models import DOOR_TIMING_METHODS, CandidateGap

type LookupDimension = Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]
type LookupTagId = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
type LookupThreshold = tuple[
    LookupTagId, Literal["exact", "at_most", "at_least"], Annotated[str, Field(max_length=128)]
]


class DoorRecordCategory(BaseModel):
    """A taxonomy category; no version means all confirmed versions.

    Type IDs match the complete pattern set; restriction IDs select a subset.
    Parameterized restrictions also require (tag ID, operator, value) thresholds.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    orientation: DoorOrientationLiteral = "Door"
    type_ids: Annotated[frozenset[LookupTagId], Field(max_length=64)] = frozenset()
    restriction_ids: Annotated[frozenset[LookupTagId], Field(max_length=64)] = frozenset()
    restriction_values: Annotated[tuple[LookupThreshold, ...], Field(max_length=64)] = ()
    version_id: Annotated[int, Field(strict=True, ge=1, le=2**15 - 1)] | None = None

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        ids = [tag_id for tag_id, _, _ in self.restriction_values]
        if len(ids) != len(set(ids)) or not set(ids) <= self.restriction_ids:
            msg = "Threshold IDs must be unique and included in restriction_ids."
            raise ValueError(msg)
        return self

    def identity_for_opening(self, opening: tuple[int, int, int]) -> CategoryIdentity:
        width, height, depth = opening
        size = f"{width}x{height}" + (f"x{depth}" if depth != 1 else "")
        types = ",".join(map(str, sorted(self.type_ids)))
        return CategoryIdentity(
            BuildKind.DOOR,
            f"door|{size}|t[{types}]|{self.orientation}",
            tuple(sorted(self.restriction_ids)),
            tuple(sorted(self.restriction_values)),
        )


class DoorRecordLookup(DoorRecordCategory):
    """A concrete opening and title in a taxonomy category."""

    opening: tuple[LookupDimension, LookupDimension, LookupDimension]
    base_title: BaseTitle = BaseTitle.SMALLEST

    @field_validator("opening", mode="before")
    @classmethod
    def validate_coordinates(cls, value: object) -> object:
        # The HTTP adapter allows coercion even for strict Pydantic fields.
        # Coordinates must not turn booleans or fractional values into sizes.
        if isinstance(value, (list, tuple)) and any(type(axis) is not int for axis in value):
            msg = "Opening coordinates must be integers."
            raise ValueError(msg)
        return value

    @property
    def identity(self) -> CategoryIdentity:
        return self.identity_for_opening(self.opening)


def evaluate_door_record(
    request: DoorRecordLookup, sources: Sequence[CandidateBuild], *, evaluated_at: datetime
) -> LiveDoorRecord:
    """Compare sources already filtered to the requested taxonomy and version."""
    configurations: list[LiveDoorConfiguration] = []
    for source in sorted(sources, key=lambda source: source.candidate.build_id):
        build = source.candidate
        if source.expansion is None:
            configurations.append(
                LiveDoorConfiguration(
                    len(configurations),
                    build.build_id,
                    None,
                    PointStatus.SUPPORTED,
                    volume=build.fixed_volume,
                    timing_variants=tuple(variant.values for variant in build.timing_variants),
                    completion_at=build.completion_at,
                )
            )
            continue
        point = evaluate_expansion(normalize_expansion(source.expansion), request.opening)
        if not point.configurations:
            configurations.append(
                LiveDoorConfiguration(
                    len(configurations),
                    build.build_id,
                    None,
                    PointStatus.UNRESOLVED,
                    diagnostics=point.diagnostics,
                )
            )
        for configuration in point.configurations:
            configurations.append(
                LiveDoorConfiguration(
                    len(configurations),
                    build.build_id,
                    configuration.branch_index,
                    configuration.status,
                    parameters=configuration.parameters,
                    volume=configuration.volume,
                    timing_variants=configuration.timing_variants,
                    diagnostics=point.diagnostics
                    + configuration.diagnostics
                    + (
                        ExpansionDiagnostic(
                            f"branches[{configuration.branch_index}].completion_time",
                            "unverified_completion",
                            "No verified completion date for this configuration.",
                        ),
                    ),
                )
            )

    return resolve_door_configurations(request, tuple(configurations), evaluated_at=evaluated_at)


def resolve_door_configurations(
    request: DoorRecordLookup,
    configurations: tuple[LiveDoorConfiguration, ...],
    *,
    evaluated_at: datetime,
) -> LiveDoorRecord:
    """Compare an already evaluated population; IDs must index the configuration tuple."""
    eligible = tuple(
        Candidate(
            configuration.id,
            completion_at=configuration.completion_at,
            fixed_volume=configuration.volume,
            timing_variants=tuple(TimingVariant(values) for values in configuration.timing_variants),
        )
        for configuration in configurations
        if configuration.membership is PointStatus.SUPPORTED
    )
    methods = tuple(method.value for method in DOOR_TIMING_METHODS)
    match request.base_title:
        case BaseTitle.FIRST:
            result = resolve_first(eligible)
        case BaseTitle.SMALLEST:
            result = resolve_smallest(eligible)
        case BaseTitle.FASTEST:
            result = resolve_fastest(eligible, methods)
        case BaseTitle.FASTEST_SMALLEST:
            result = resolve_fastest_smallest(eligible, methods)
        case BaseTitle.SMALLEST_FASTEST:
            result = resolve_smallest_fastest(eligible, methods)

    uncertain = tuple(c.id for c in configurations if c.membership is PointStatus.UNRESOLVED)
    if uncertain:
        result = TitleResolution(
            ResolutionStatus.UNRESOLVED,
            provisional_holder_ids=tuple(sorted(set(result.holder_ids + result.provisional_holder_ids + uncertain))),
            gaps=result.gaps + tuple(CandidateGap(id, "expansion_membership") for id in uncertain),
        )

    def build_ids(ids: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(sorted({configurations[id].build_id for id in ids}))

    resolution = TitleResolution(
        result.status,
        holder_ids=build_ids(result.holder_ids),
        provisional_holder_ids=build_ids(result.provisional_holder_ids),
        gaps=tuple(
            dict.fromkeys(CandidateGap(configurations[gap.build_id].build_id, gap.field) for gap in result.gaps)
        ),
    )
    # Ties between alternatives of the same build cannot change the build-level
    # holder, even when missing facts prevent selecting its winning branch.
    if (
        not uncertain
        and resolution.status is ResolutionStatus.UNRESOLVED
        and len(resolution.provisional_holder_ids) == 1
    ):
        resolution = TitleResolution(ResolutionStatus.RESOLVED, holder_ids=resolution.provisional_holder_ids)
    return LiveDoorRecord(
        request.opening,
        request.base_title,
        request.version_id,
        evaluated_at,
        resolution,
        result.holder_ids,
        result.provisional_holder_ids,
        tuple(configurations),
    )
