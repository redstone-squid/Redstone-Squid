"""Persistence boundaries for range snapshots and publication."""

from collections.abc import Sequence
from typing import Protocol

from squid.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery
from squid.records.application.ports import RecordUnitOfWork
from squid.records.application.range_snapshots import RangeAttempt, RangePublication, RangeSnapshotDocument


class RangePublicationRepository(Protocol):
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


class RangePublicationUnitOfWork(RecordUnitOfWork, Protocol):
    @property
    def record_ranges(self) -> RangePublicationRepository: ...
