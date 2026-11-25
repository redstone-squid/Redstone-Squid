"""Application data transferred between record computation and persistence."""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from squid.core.builds.application.queries import PublicBuildSummary
from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    CandidateGap,
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RecordCriterion,
    RecordFact,
    RecordResolution,
    VersionScope,
)
from squid.core.records.domain.door_evidence import DoorRecordEvidence
from squid.core.records.domain.models import MaterializationSource, RecomputeTarget, RecordTagRole


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


type ScopedCategory = tuple[int | None, str]
"""A category key within one version scope; a null version means all versions."""


@dataclass(frozen=True, slots=True)
class ComputationBatch:
    """The recomputed records of some categories of one build kind.

    `categories` is the affected set. Records in it that `records` leaves out have
    no competitors and retire. `expected` is set by a backstop: the categories
    pending changes account for, so a change outside them is drift.
    """

    rules_revision_id: int
    kind: BuildKind
    input_generation: int
    categories: frozenset[ScopedCategory]
    records: tuple[ComputedRecordResult, ...]
    expected: frozenset[ScopedCategory] | None = None
    full: bool = False


@dataclass(frozen=True, slots=True)
class RecordGap:
    """A published record that missing facts could change, and those facts."""

    definition_id: int
    title: str
    subtitle: str | None
    criterion: RecordCriterion
    build_ids: tuple[int, ...]
    fields: tuple[RecordFact, ...]
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
    """A record's current result, or a retired record's last name.

    `record_id` stays stable for the record's lifetime. `result_id` identifies the
    current result and stays the same while it is updated; it is `None` once the
    record retires, which happens when no build competes for it. `definition_id`
    identifies the record's wording under one rules revision.
    """

    result_id: int | None
    definition_id: int
    record_id: UUID
    title: str
    subtitle: str | None
    criterion: RecordCriterion
    build_kind: BuildKind
    version_scope: VersionScope
    holder_build_ids: tuple[int, ...]
    computed_at: datetime
    tied_build_ids: tuple[int, ...] = field(default=(), kw_only=True)
    gaps: tuple[CandidateGap, ...] = field(default=(), kw_only=True)
    last_checked_at: datetime | None = field(default=None, kw_only=True)
    refresh_pending: bool = field(default=False, kw_only=True)
    version_id: int | None = field(default=None, kw_only=True)
    door_evidence: DoorRecordEvidence | None = field(default=None, kw_only=True)
    retired_at: datetime | None = field(default=None, kw_only=True)

    @property
    def one_line_title(self) -> str:
        """The title and subtitle on one line; many records share a title and differ only by subtitle."""
        return f"{self.title} · {self.subtitle}" if self.subtitle else self.title


@dataclass(frozen=True, slots=True)
class RecordViewer:
    subscription_id: int | None


@dataclass(frozen=True, slots=True)
class PublicRecordDetail(PublishedRecordResult):
    """One record with the public builds it names.

    `holder_builds` and `tied_builds` follow their ID fields. `contender_builds` are
    the non-holders named by `gaps`: builds that could take the record once their
    missing facts are known. `history` lists past and current holder periods; a
    retired record keeps it with its last period closed. Hidden builds are left
    out. The REST API serializes this type as it is, so a new field changes the
    published contract.
    """

    holder_builds: tuple[PublicBuildSummary, ...]
    tied_builds: tuple[PublicBuildSummary, ...] = ()
    contender_builds: tuple[PublicBuildSummary, ...] = ()
    history: tuple[HolderHistoryEntry, ...] = ()
    viewer: RecordViewer | None = None

    @property
    def contender_build_ids(self) -> tuple[int, ...]:
        """Gap builds other than the holders, in gap order."""
        holders = set(self.holder_build_ids)
        return tuple(dict.fromkeys(gap.build_id for gap in self.gaps if gap.build_id not in holders))

    @classmethod
    def with_builds(
        cls,
        result: PublishedRecordResult,
        builds: Mapping[int, PublicBuildSummary],
        history: tuple[HolderHistoryEntry, ...] = (),
    ) -> Self:
        """Attach public summaries; every holder must be present in `builds`."""
        detail = cls(**{field.name: getattr(result, field.name) for field in fields(result)}, holder_builds=())
        return replace(
            detail,
            history=history,
            holder_builds=tuple(builds[id] for id in result.holder_build_ids),
            tied_builds=tuple(builds[id] for id in result.tied_build_ids if id in builds),
            contender_builds=tuple(builds[id] for id in detail.contender_build_ids if id in builds),
        )


@dataclass(frozen=True, slots=True)
class RecordLookupRequest:
    """An exact category to keep in every maintained version scope."""

    kind: BuildKind
    base_key: str
    restriction_ids: frozenset[int]
    restriction_values: tuple[tuple[int, str, str], ...] = ()


def _render_scalar(value: Decimal | str | bool) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return str(value).lower()
    return value


@dataclass(frozen=True, slots=True)
class RebuildSummary:
    """What one or more batches published.

    `records` counts the recomputed records that have holders, `changed` those
    whose published result changed, and `retired` those left without competitors.
    `drift` counts backstop differences that no pending change explained.
    """

    run_ids: tuple[int, ...] = ()
    records: int = 0
    changed: int = 0
    retired: int = 0
    with_gaps: int = 0
    drift: int = 0

    def __add__(self, other: RebuildSummary) -> RebuildSummary:
        return RebuildSummary(
            self.run_ids + other.run_ids,
            self.records + other.records,
            self.changed + other.changed,
            self.retired + other.retired,
            self.with_gaps + other.with_gaps,
            self.drift + other.drift,
        )


@dataclass(frozen=True, slots=True)
class QueueProcessSummary:
    """The build kinds whose queued work was published."""

    kinds: tuple[BuildKind, ...]
    rebuild: RebuildSummary | None


@dataclass(frozen=True, slots=True)
class PriorPublication:
    """What is published now, as far as working out an affected set needs.

    `live` holds every category with a current result. `memberships` holds, for
    each build the batch changes, the live categories it was a member of.
    """

    live: frozenset[ScopedCategory] = frozenset()
    memberships: Mapping[int, frozenset[ScopedCategory]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecomputeItem:
    """One queued request, as stored in `record_recompute_queue`."""

    id: int
    kind: BuildKind
    target: RecomputeTarget
    build_id: int | None
    record_id: UUID | None
    reasons: tuple[str, ...]
    enqueued_at: datetime
    attempts: int
    last_error: str | None
    claimed: bool


@dataclass(frozen=True, slots=True)
class RecordDrift:
    """A backstop difference: a published record the affected sets missed."""

    id: int
    record_id: UUID
    title: str
    run_id: int
    detected_at: datetime
    published: Mapping[str, object]
    computed: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RecordInspection:
    """A fresh computation of one record compared with what is published.

    `computed_*` comes from current builds and is not published. Members are the
    builds in the record's category; `gained` and `lost` compare them with the
    stored membership index. A computed record without holders would retire.
    """

    record_id: UUID
    title: str
    published_holder_ids: tuple[int, ...]
    computed_holder_ids: tuple[int, ...]
    published_gaps: tuple[CandidateGap, ...]
    computed_gaps: tuple[CandidateGap, ...]
    members_gained: tuple[int, ...]
    members_lost: tuple[int, ...]

    @property
    def matches(self) -> bool:
        return (
            self.published_holder_ids == self.computed_holder_ids
            and self.published_gaps == self.computed_gaps
            and not self.members_gained
            and not self.members_lost
        )
