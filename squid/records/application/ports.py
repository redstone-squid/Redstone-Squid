"""Ports used by record application services."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.records.application.models import (
    CategoryIdentity,
    ComputationBatch,
    PublishedRecord,
    RecordGap,
    RecordSourceCandidate,
    TitleDiagnosticGap,
)
from squid.records.domain import BuildKind, CategoryText, RecordClass


@dataclass(frozen=True, slots=True)
class RecomputeLease:
    """A set of recompute scopes leased together by one worker.

    The kinds are what gets rebuilt; the tokens are what the acknowledgement is
    fenced on. Carrying the tokens is what distinguishes the rows this worker
    leased from rows enqueued while it was running, which acknowledging by kind
    alone silently destroyed.
    """

    kinds: tuple[BuildKind, ...]
    claim_tokens: tuple[uuid.UUID, ...]

    def __bool__(self) -> bool:
        return bool(self.kinds)


class RecordCandidateRepository(Protocol):
    """Load confirmed builds eligible to become computation candidates."""

    async def list_confirmed(self, kind: BuildKind) -> Sequence[RecordSourceCandidate]: ...


class RecordRunRepository(Protocol):
    """Persist and inspect versioned record computation runs."""

    async def active_ruleset_id(self) -> int: ...

    async def active_current_version_id(self) -> int | None: ...

    async def activate(self, batch: ComputationBatch) -> int: ...

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]: ...

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]: ...

    async def get_published_record(self, result_id: int) -> PublishedRecord | None: ...

    async def list_published_records(
        self,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        descending: bool,
        limit: int,
    ) -> Sequence[PublishedRecord]: ...

    async def count_published_records(self) -> int: ...

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]: ...

    async def save_requested_category(
        self,
        ruleset_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordClass, CategoryText],
    ) -> None: ...

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None: ...

    async def claim_recompute_kinds(self, *, limit: int) -> RecomputeLease: ...

    async def complete_recompute(self, lease: RecomputeLease) -> None: ...

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None: ...
