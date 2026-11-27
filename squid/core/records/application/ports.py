"""Ports used by record application services."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.core.actors import ActingContext
from squid.core.builds.application.queries import PublicBuildSummary
from squid.core.pagination import PageSelector
from squid.core.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery
from squid.core.records.application.models import (
    CandidateBuild,
    CategoryIdentity,
    ComputationBatch,
    PriorPublication,
    PublishedRecordResult,
    RebuildSummary,
    RecomputeItem,
    RecomputeTarget,
    RecordDrift,
    RecordGap,
    RecordHolderPeriod,
    RecordListFilters,
    RecordListSort,
    RecordNameDiagnostic,
    RecordSibling,
    ScopedCategory,
)
from squid.core.records.domain import BuildKind, CandidateGap, FormattedTitle, RecordCriterion
from squid.core.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class RecomputeLease:
    """Queued items of one kind, claimed together and acknowledged by their tokens.

    A full lease recomputes every category; `backstop` also reports drift.
    `builds` and `records` are the changed builds and requested records; a full
    lease still carries them so the backstop can tell expected changes apart.
    An empty lease has no kind.
    """

    kind: BuildKind | None
    claim_tokens: tuple[uuid.UUID, ...] = ()
    full: bool = False
    backstop: bool = False
    builds: frozenset[int] = frozenset()
    records: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """A record's identity and what is published for it, for inspection."""

    record_id: uuid.UUID
    title: str
    kind: BuildKind
    criterion: RecordCriterion
    version_id: int | None
    category_key: str
    holder_build_ids: tuple[int, ...]
    gaps: tuple[CandidateGap, ...]
    member_build_ids: tuple[int, ...]


class RecordCandidateRepository(Protocol):
    """Load approved builds eligible to become computation candidates."""

    async def begin_computation(self) -> None:
        """Pin a consistent read/write snapshot; call first in a fresh unit of work."""
        ...

    async def list_confirmed(self, kind: BuildKind) -> Sequence[CandidateBuild]: ...

    async def list_for_door_widths(
        self,
        height: int,
        depth: int,
        orientation: str,
        version_id: int | None,
        *,
        minimum: int,
        maximum: int | None,
    ) -> Sequence[CandidateBuild]:
        """Read a consistent width population; call first in a fresh unit of work.

        Fixed doors retain their opening dimensions. Families have no fixed
        opening and still require domain analysis before competing.
        """
        ...


class DoorRangeAnalyzer(Protocol):
    """Run admitted arithmetic outside the caller's database transaction."""

    async def analyze(self, sources: Sequence[CandidateBuild], query: DoorRangeQuery) -> DoorRangeAnalysis: ...


class PublicBuildSummaryReader(Protocol):
    """Load only build fields approved for public record details."""

    async def get_public_summaries(
        self, build_ids: Sequence[int], *, context: ActingContext
    ) -> Sequence[PublicBuildSummary]: ...


class RecordRunRepository(Protocol):
    """Persist current record results and the queue of work that recomputes them."""

    async def active_rules_revision_id(self) -> int: ...

    async def active_current_version_id(self) -> int | None: ...

    async def set_current_version(self, version_id: int | None) -> None: ...

    async def input_generation(self, kind: BuildKind) -> int: ...

    async def prior_publication(self, kind: BuildKind, build_ids: frozenset[int]) -> PriorPublication: ...

    async def pending_builds(self, kind: BuildKind) -> frozenset[int]:
        """Return every build with queued work for `kind`, claimed or not."""
        ...

    async def record_categories(self, record_ids: frozenset[uuid.UUID]) -> frozenset[ScopedCategory]: ...

    async def publish(self, batch: ComputationBatch) -> RebuildSummary: ...

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]: ...

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordNameDiagnostic]: ...

    async def get_published_record(self, result_id: int) -> PublishedRecordResult | None: ...

    async def get_latest_record(self, record_id: uuid.UUID) -> PublishedRecordResult | None:
        """Return the current result, or a retired record's last name without holders."""
        ...

    async def get_record_history(self, record_id: uuid.UUID) -> tuple[RecordHolderPeriod, ...]: ...

    async def list_siblings(self, record_id: uuid.UUID) -> tuple[RecordSibling, ...]: ...

    async def stored_record(self, record_id: uuid.UUID) -> StoredRecord | None: ...

    async def list_published_records(
        self, *, selector: PageSelector, sort: RecordListSort, filters: RecordListFilters, limit: int
    ) -> Sequence[PublishedRecordResult]: ...

    async def count_published_records(self, *, filters: RecordListFilters) -> int: ...

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]: ...

    async def definition_record_id(self, definition_id: int) -> uuid.UUID | None: ...

    async def save_requested_category(
        self,
        rules_revision_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordCriterion, FormattedTitle],
    ) -> int:
        """Save a category in every maintained scope and queue its records; return how many."""
        ...

    async def request_record_recompute(self, record_id: uuid.UUID) -> bool: ...

    async def request_full_recompute(self, kinds: Sequence[BuildKind], *, target: RecomputeTarget) -> None: ...

    async def claim_recompute(self, *, limit: int) -> RecomputeLease: ...

    async def complete_recompute(self, lease: RecomputeLease) -> None: ...

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None: ...

    async def list_recompute_queue(self) -> Sequence[RecomputeItem]: ...

    async def list_drift(
        self, *, offset: int, after_id: int | None, before_id: int | None, limit: int
    ) -> Sequence[RecordDrift]: ...

    async def count_drift(self) -> int: ...


class RecordQueryUnitOfWork(UnitOfWork, Protocol):
    @property
    def records(self) -> RecordRunRepository: ...


class RecordUnitOfWork(RecordQueryUnitOfWork, Protocol):
    @property
    def record_candidates(self) -> RecordCandidateRepository: ...
