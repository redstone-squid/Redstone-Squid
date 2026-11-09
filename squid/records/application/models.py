"""Application data transferred between record computation and persistence."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from squid.builds.application.queries import PublicBuildSummary
from squid.records.domain import (
    BaseTitle,
    BuildKind,
    Candidate,
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    ResolutionStatus,
    TitleResolution,
    VersionScope,
)
from squid.records.domain.models import FacetKind, MaterializationSource


@dataclass(frozen=True, slots=True)
class CandidateFacet:
    """A taxonomy value and the category threshold a candidate can satisfy.

    `assigned_value` is the build's actual value. `category_value` overrides it
    for category titles: a build three blocks wide can enter an at-most-four-wide
    category while keeping its measured width of three.
    """

    id: int
    kind: FacetKind
    name: str
    restriction_type: str | None = None
    stable_key: str | None = None
    value_type: str = "none"
    assigned_value: Decimal | str | bool | None = None
    category_value: Decimal | str | bool | None = None
    record_operator: str | None = None
    render_template: str = "{name}"
    display_unit: str | None = None
    display_scale: Decimal = Decimal(1)

    @property
    def category_name(self) -> str:
        """Render the category threshold represented by this facet."""
        value = self.category_value if self.category_value is not None else self.assigned_value
        if value is None:
            return self.name
        if isinstance(value, Decimal):
            value *= self.display_scale
        rendered_value = _render_scalar(value)
        unit = self.display_unit or ""
        return self.render_template.format(name=self.name, value=rendered_value, unit=unit).strip()


@dataclass(frozen=True, slots=True)
class CandidateBuild:
    """A confirmed build plus the facts needed to derive its categories."""

    kind: BuildKind
    candidate: Candidate
    version_ids: frozenset[int]
    restrictions: tuple[CandidateFacet, ...]
    types: tuple[CandidateFacet, ...]
    door: DoorCategory | None = None
    extender: ExtenderCategory | None = None


@dataclass(frozen=True, slots=True)
class CategoryIdentity:
    """A stable category category shared by qualifying builds.

    Callers provide sorted restriction IDs and values; `key` preserves their order.
    """

    kind: BuildKind
    base_key: str
    restriction_ids: tuple[int, ...]
    restriction_values: tuple[tuple[int, str, str], ...] = ()

    @property
    def key(self) -> str:
        restriction_key = ",".join(str(facet_id) for facet_id in self.restriction_ids)
        value_key = ",".join(f"{tag_id}:{operator}:{value}" for tag_id, operator, value in self.restriction_values)
        return f"{self.kind.value}:{self.base_key}:r[{restriction_key}]:p[{value_key}]"


@dataclass(frozen=True, slots=True)
class CategoryCandidates:
    """Candidates and presentation facts for one category."""

    identity: CategoryIdentity
    facets: tuple[CandidateFacet, ...]
    category_text: FormattedTitle
    candidates: tuple[Candidate, ...]
    candidate_version_ids: tuple[tuple[int, frozenset[int]], ...]
    source: MaterializationSource = MaterializationSource.EAGER


@dataclass(frozen=True, slots=True)
class ComputedRecord:
    """One record definition and its calculated outcome."""

    base_title: BaseTitle
    scope: VersionScope
    version_id: int | None
    category: CategoryCandidates
    title: FormattedTitle
    resolution: TitleResolution
    broken_holder_ids: tuple[int, ...] = ()
    history: tuple[HolderHistoryEntry, ...] = ()
    history_complete: bool = True


@dataclass(frozen=True, slots=True)
class HolderHistoryEntry:
    """A reconstructed period in which one or more builds held a record."""

    build_ids: tuple[int, ...]
    held_from: datetime
    held_until: datetime | None


@dataclass(frozen=True, slots=True)
class ComputationBatch:
    """A replacement run for one build kind; a null version includes all versions."""

    rules_revision_id: int
    kind: BuildKind
    version_id: int | None
    records: tuple[ComputedRecord, ...]


@dataclass(frozen=True, slots=True)
class RecordGap:
    """A persisted unresolved record and its decisive missing facts."""

    definition_id: int
    title: str
    subtitle: str | None
    base_title: BaseTitle
    build_ids: tuple[int, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TitleDiagnosticGap:
    """A canonical record title containing unknown or contradictory taxonomy."""

    definition_id: int
    title: str
    diagnostics: tuple[dict[str, str | list[str]], ...]


@dataclass(frozen=True, slots=True)
class PublishedTitle:
    """One result belonging to the active computation run for its kind and version.

    `id` identifies this run's result. `definition_id` identifies a definition within
    its rules revision, while `title_id` stays stable across rules revisions and runs.
    """

    id: int
    definition_id: int
    title_id: UUID
    title: str
    subtitle: str | None
    base_title: BaseTitle
    build_kind: BuildKind
    version_scope: VersionScope
    status: ResolutionStatus
    holder_build_ids: tuple[int, ...]
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class PublicRecordDetail:
    """One published result with its complete ordered public holders."""

    result: PublishedTitle
    holder_builds: tuple[PublicBuildSummary, ...]


@dataclass(frozen=True, slots=True)
class RecordLookupRequest:
    """An exact category requested for persistent materialization."""

    kind: BuildKind
    base_key: str
    restriction_ids: frozenset[int]
    restriction_values: tuple[tuple[int, str, str], ...] = ()
    version_id: int | None = None


def _render_scalar(value: Decimal | str | bool) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return str(value).lower()
    return value


@dataclass(frozen=True, slots=True)
class RebuildSummary:
    """Counts produced by rebuilding all requested record scopes."""

    run_ids: tuple[int, ...]
    definitions: int
    resolved: int
    unresolved: int


@dataclass(frozen=True, slots=True)
class QueueProcessSummary:
    """The record scopes claimed and rebuilt from the durable queue."""

    kinds: tuple[BuildKind, ...]
    rebuild: RebuildSummary | None
