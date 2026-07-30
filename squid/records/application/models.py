"""Application data transferred between record computation and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

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

    @property
    def key(self) -> str:
        restriction_key = ",".join(str(facet_id) for facet_id in self.restriction_ids)
        return f"{self.kind.value}:{self.base_key}:r[{restriction_key}]"


@dataclass(frozen=True, slots=True)
class CategoryCompetition:
    """Candidates and presentation facts for one category."""

    identity: CategoryIdentity
    facets: tuple[CandidateFacet, ...]
    category_text: CategoryText
    candidates: tuple[RecordCandidate, ...]
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
class RecordLookupRequest:
    """An exact category requested for persistent materialization."""

    kind: BuildKind
    base_key: str
    restriction_ids: frozenset[int]
    version_id: int | None = None


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
