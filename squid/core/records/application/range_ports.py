"""Persistence boundaries for range snapshots and publication."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from squid.core.pagination import Page, PageSelector
from squid.core.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery, RangeLabels
from squid.core.records.application.live import DoorRecordCategory
from squid.core.records.application.ports import RecordUnitOfWork
from squid.core.records.application.range_snapshots import (
    RangeAttempt,
    RangePublication,
    RangeRegistration,
    RangeSnapshotDocument,
)


@dataclass(frozen=True, slots=True)
class RangeListFilters:
    orientations: tuple[str, ...] = ()
    heights: tuple[int, ...] = ()
    depths: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RangeRefreshClaim:
    query_id: int
    token: UUID
    query: DoorRangeQuery


class RangePublicationRepository(Protocol):
    async def claim_refresh(self) -> RangeRefreshClaim | None: ...

    async def complete_refresh(self, claim: RangeRefreshClaim) -> None: ...

    async def fail_refresh(self, claim: RangeRefreshClaim, error: str) -> None: ...

    async def register(self, query: DoorRangeQuery) -> int: ...

    async def request_refresh(self, query_id: int) -> bool: ...

    async def list_registered(self) -> Sequence[tuple[int, DoorRangeQuery]]: ...

    async def begin(self, query_id: int, rules_revision_id: int) -> RangeAttempt: ...

    async def save_snapshot(self, run_id: int, document: RangeSnapshotDocument) -> None: ...

    async def get_snapshot(self, run_id: int) -> RangeSnapshotDocument | None: ...

    async def finish(self, run_id: int, result: DoorRangeAnalysis | None, *, error: str | None = None) -> None:
        """Complete once, activating exact results only for the latest generation."""
        ...

    async def get(self, query_id: int) -> RangePublication | None: ...

    async def list_registrations(
        self, filters: RangeListFilters, *, selector: PageSelector, page_size: int
    ) -> Page[RangeRegistration]:
        """List registrations newest first."""
        ...

    async def labels(self, queries: Sequence[tuple[DoorRecordCategory, int | None]]) -> tuple[RangeLabels, ...]:
        """Name each category's tags and version, in the order given."""
        ...


class RangePublicationUnitOfWork(RecordUnitOfWork, Protocol):
    @property
    def record_ranges(self) -> RangePublicationRepository: ...
