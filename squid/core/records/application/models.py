"""Application data transferred between record computation and persistence."""

from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from squid.core.builds.application.queries import PublicBuildSummary
from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RecordCriterion,
    RecordResolution,
    ResolutionStatus,
    VersionScope,
)
from squid.core.records.domain.door_evidence import DoorRecordEvidence
from squid.core.records.domain.models import MaterializationSource, RecordTagRole


@dataclass(frozen=True, slots=True)
class CandidateTag:
    """A tag value and the category threshold a candidate can satisfy.

    `assigned_value` is the build's actual value. `category_value` overrides it
    for category titles: a build three blocks wide can enter an at-most-four-wide
    category while keeping its measured width of three.
    """

    id: int
    kind: RecordTagRole
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
        """Render the category threshold represented by this tag."""
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
    """A approved build plus the facts needed to derive its categories."""

    kind: BuildKind
    candidate: Candidate
    version_ids: frozenset[int]
    restrictions: tuple[CandidateTag, ...]
    types: tuple[CandidateTag, ...]
    door: DoorCategory | None = None
    extender: ExtenderCategory | None = None
    expansion: DoorExpansion | None = None
    door_opening: tuple[int, int, int] | None = None


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
        restriction_key = ",".join(str(tag_id) for tag_id in self.restriction_ids)
        value_key = ",".join(f"{tag_id}:{operator}:{value}" for tag_id, operator, value in self.restriction_values)
        return f"{self.kind.value}:{self.base_key}:r[{restriction_key}]:p[{value_key}]"

    @classmethod
    def from_key(cls, key: str) -> Self | None:
        """Parse a stored category key, returning `None` for unrecognized syntax.

        This checks the serialized structure, not whether the referenced tags exist
        or their operators and values form a valid category.
        """
        try:
            kind_value, remainder = key.split(":", maxsplit=1)
            base_key, restriction_part = remainder.rsplit(":r[", maxsplit=1)
            raw_ids, separator, raw_values = restriction_part.partition("]:p[")
            if not separator or not raw_values.endswith("]"):
                return None
            restriction_ids = tuple(int(value) for value in raw_ids.split(",") if value)
            values = tuple(
                (int(tag_id), operator, value)
                for item in raw_values.removesuffix("]").split(",")
                if item
                for tag_id, operator, value in (item.split(":", maxsplit=2),)
            )
            return cls(BuildKind(kind_value), base_key, restriction_ids, values)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class CategoryCandidates:
    """Candidates and presentation facts for one category."""

    identity: CategoryIdentity
    tags: tuple[CandidateTag, ...]
    category_text: FormattedTitle
    candidates: tuple[Candidate, ...]
    source: MaterializationSource = MaterializationSource.EAGER


@dataclass(frozen=True, slots=True)
class ComputedRecordResult:
    """One record definition and its calculated outcome."""

    criterion: RecordCriterion
    scope: VersionScope
    version_id: int | None
    category: CategoryCandidates
    title: FormattedTitle
    resolution: RecordResolution
    history: tuple[HolderHistoryEntry, ...] = ()
    history_complete: bool = True
    door_evidence: DoorRecordEvidence | None = None


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
    records: tuple[ComputedRecordResult, ...]


@dataclass(frozen=True, slots=True)
class RecordGap:
    """A persisted unresolved record and its decisive missing facts."""

    definition_id: int
    title: str
    subtitle: str | None
    criterion: RecordCriterion
    build_ids: tuple[int, ...]
    fields: tuple[str, ...]
    build_kind: BuildKind


@dataclass(frozen=True, slots=True)
class RecordNameDiagnostic:
    """A record name with unknown or contradictory tags."""

    definition_id: int
    title: str
    diagnostics: tuple[dict[str, str | list[str]], ...]
    build_kind: BuildKind


@dataclass(frozen=True, slots=True)
class PublishedRecordResult:
    """One result belonging to the active computation run for its kind and version.

    `result_id` identifies this run's result. `definition_id` identifies a definition within
    its rules revision, while `record_id` stays stable across rules revisions and runs.
    """

    result_id: int
    definition_id: int
    record_id: UUID
    title: str
    subtitle: str | None
    criterion: RecordCriterion
    build_kind: BuildKind
    version_scope: VersionScope
    status: ResolutionStatus
    holder_build_ids: tuple[int, ...]
    computed_at: datetime
    last_checked_at: datetime | None = field(default=None, kw_only=True)
    refresh_pending: bool = field(default=False, kw_only=True)
    version_id: int | None = field(default=None, kw_only=True)
    door_evidence: DoorRecordEvidence | None = field(default=None, kw_only=True)

    @property
    def one_line_title(self) -> str:
        """The title and subtitle on one line; many records share a title and differ only by subtitle."""
        return f"{self.title} · {self.subtitle}" if self.subtitle else self.title


@dataclass(frozen=True, slots=True)
class RecordViewer:
    subscription_id: int | None


@dataclass(frozen=True, slots=True)
class PublicRecordDetail(PublishedRecordResult):
    """One published result with its complete ordered public holders.

    `holder_builds` follows the order of `holder_build_ids`. The REST API serializes
    this type as it is, so a new field changes the published contract.
    """

    holder_builds: tuple[PublicBuildSummary, ...]
    viewer: RecordViewer | None = None

    @classmethod
    def with_holders(cls, result: PublishedRecordResult, holder_builds: Sequence[PublicBuildSummary]) -> Self:
        """Attach the holders' public summaries to a published result."""
        return cls(
            **{field.name: getattr(result, field.name) for field in fields(result)},
            holder_builds=tuple(holder_builds),
        )


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
