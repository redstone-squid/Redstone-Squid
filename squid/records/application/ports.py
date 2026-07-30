"""Ports used by record application services."""

from collections.abc import Sequence
from typing import Protocol

from squid.records.application.models import (
    CategoryIdentity,
    ComputationBatch,
    RecordGap,
    RecordSourceCandidate,
)
from squid.records.domain import BuildKind


class RecordCandidateRepository(Protocol):
    """Load confirmed builds eligible to become computation candidates."""

    async def list_confirmed(self, kind: BuildKind) -> Sequence[RecordSourceCandidate]: ...


class RecordRunRepository(Protocol):
    """Persist and inspect versioned record computation runs."""

    async def active_ruleset_id(self) -> int: ...

    async def activate(self, batch: ComputationBatch) -> int: ...

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]: ...

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]: ...

    async def save_requested_category(self, ruleset_id: int, category: CategoryIdentity) -> None: ...

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None: ...

    async def claim_recompute_kinds(self, *, limit: int) -> Sequence[BuildKind]: ...

    async def complete_recompute(self, kinds: Sequence[BuildKind]) -> None: ...

    async def fail_recompute(self, kinds: Sequence[BuildKind], error: str) -> None: ...
