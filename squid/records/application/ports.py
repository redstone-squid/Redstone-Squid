"""Ports used by record application services."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.builds.application.queries import PublicBuildSummary
from squid.records.application.models import (
    CandidateBuild,
    CategoryIdentity,
    ComputationBatch,
    PublishedTitle,
    RecordGap,
    TitleDiagnosticGap,
)
from squid.records.domain import BaseTitle, BuildKind, FormattedTitle
from squid.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class RecomputeLease:
    """Queued scopes claimed together, with distinct kinds to rebuild and tokens to acknowledge.

    Several scopes can belong to one kind, so the tuples need not have equal
    lengths. Acknowledging tokens preserves work enqueued or reclaimed during
    this worker's computation.
    """

    kinds: tuple[BuildKind, ...]
    claim_tokens: tuple[uuid.UUID, ...]

    def __bool__(self) -> bool:
        return bool(self.kinds)


class RecordCandidateRepository(Protocol):
    """Load confirmed builds eligible to become computation candidates."""

    async def list_confirmed(self, kind: BuildKind) -> Sequence[CandidateBuild]: ...


class PublicBuildSummaryReader(Protocol):
    """Load only build fields approved for public record details."""

    async def get_public_summaries(self, build_ids: Sequence[int]) -> Sequence[PublicBuildSummary]: ...


class RecordRunRepository(Protocol):
    """Persist and inspect versioned record computation runs."""

    async def active_rules_revision_id(self) -> int: ...

    async def active_current_version_id(self) -> int | None: ...

    async def activate(self, batch: ComputationBatch) -> int: ...

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]: ...

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]: ...

    async def get_published_record(self, result_id: int) -> PublishedTitle | None: ...

    async def list_published_records(
        self,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        descending: bool,
        limit: int,
    ) -> Sequence[PublishedTitle]: ...

    async def count_published_records(self) -> int: ...

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]: ...

    async def get_definition_identity(self, definition_id: int) -> CategoryIdentity | None: ...

    async def save_requested_category(
        self,
        rules_revision_id: int,
        category: CategoryIdentity,
        titles: Mapping[BaseTitle, FormattedTitle],
    ) -> None: ...

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None: ...

    async def claim_recompute_kinds(self, *, limit: int) -> RecomputeLease: ...

    async def complete_recompute(self, lease: RecomputeLease) -> None: ...

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None: ...


class RecordQueryUnitOfWork(UnitOfWork, Protocol):
    @property
    def records(self) -> RecordRunRepository: ...


class RecordUnitOfWork(RecordQueryUnitOfWork, Protocol):
    @property
    def record_candidates(self) -> RecordCandidateRepository: ...
