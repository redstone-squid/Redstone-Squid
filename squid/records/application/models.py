"""Application data transferred between record computation and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from squid.records.domain import (
    BuildKind,
    CategoryText,
    DoorCategory,
    ExtenderCategory,
    RecordCandidate,
    RecordClass,
    RecordResolution,
    VersionScope,
)

FacetKind = Literal["restriction", "type", "pattern", "category"]
MaterializationSource = Literal["eager", "seeded", "public_lookup"]


@dataclass(frozen=True, slots=True)
class CandidateFacet:
    """A canonical taxonomy value attached to a candidate."""

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

    @property
    def category_name(self) -> str:
        """Render the category threshold represented by this facet."""
        value = self.category_value if self.category_value is not None else self.assigned_value
        if value is None:
            return self.name
        rendered_value = _render_scalar(value)
        unit = self.display_unit or ""
        return self.render_template.format(name=self.name, value=rendered_value, unit=unit).strip()


@dataclass(frozen=True, slots=True)
class RecordSourceCandidate:
    """A confirmed build plus the facts needed to derive its categories."""

    kind: BuildKind
    candidate: RecordCandidate
    version_ids: frozenset[int]
    restrictions: tuple[CandidateFacet, ...]
    types: tuple[CandidateFacet, ...]
    door: DoorCategory | None = None
    extender: ExtenderCategory | None = None


@dataclass(frozen=True, slots=True)
class CategoryIdentity:
    """A stable competition category shared by qualifying builds."""

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
class CategoryCompetition:
    """Candidates and presentation facts for one category."""

    identity: CategoryIdentity
    facets: tuple[CandidateFacet, ...]
    category_text: CategoryText
    candidates: tuple[RecordCandidate, ...]
    candidate_version_ids: tuple[tuple[int, frozenset[int]], ...]
    source: MaterializationSource = "eager"


@dataclass(frozen=True, slots=True)
class ComputedRecord:
    """One record definition and its calculated outcome."""

    record_class: RecordClass
    scope: VersionScope
    version_id: int | None
    competition: CategoryCompetition
    title: CategoryText
    resolution: RecordResolution
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
    """A complete replacement run for a build kind and concrete version."""

    ruleset_id: int
    kind: BuildKind
    version_id: int | None
    records: tuple[ComputedRecord, ...]


@dataclass(frozen=True, slots=True)
class RecordGap:
    """A persisted unresolved record and its decisive missing facts."""

    definition_id: int
    category_key: str
    record_class: RecordClass
    build_ids: tuple[int, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TitleDiagnosticGap:
    """A canonical record title containing unknown or contradictory taxonomy."""

    definition_id: int
    title: str
    diagnostics: tuple[dict[str, str | list[str]], ...]


@dataclass(frozen=True, slots=True)
class PublishedRecord:
    """Public read model for one result of the currently published computation run.

    It used to be `ActiveRecord`, which borrows Rails' name for "a domain object
    that persists itself" to mean something entirely different. It persists
    nothing and knows nothing about its own storage.

    `record_computation_runs.is_active` keeps the word and is right to: it
    describes a *run*, and exactly one run per kind and version is the active
    one. What this type names is a result belonging to that run, which is a
    different fact about a different thing.
    """

    id: int
    definition_id: int
    competition_id: UUID
    title: str
    subtitle: str | None
    record_class: str
    build_kind: str
    version_scope: str
    status: str
    holder_build_ids: tuple[int, ...]
    computed_at: datetime


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
