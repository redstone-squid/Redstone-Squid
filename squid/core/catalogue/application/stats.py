"""Headline counts for the public catalogue."""

from dataclasses import dataclass
from typing import Protocol

from squid.core.actors import ActingContext, application_operation
from squid.core.builds.domain import BuildStatus
from squid.core.records.application.models import ALL_RECORDS, RecordListFilters
from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class CatalogueStats:
    """Counts of approved builds, current records, and the people credited on them."""

    builds: int
    records: int
    creators: int


class _BuildCounts(Protocol):
    async def count(self, *, statuses: frozenset[BuildStatus]) -> int: ...

    async def count_creators(self) -> int: ...


class _RecordCounts(Protocol):
    async def count_published_records(self, *, filters: RecordListFilters) -> int: ...


class CatalogueStatsUnitOfWork(UnitOfWork, Protocol):
    @property
    def builds(self) -> _BuildCounts: ...

    @property
    def records(self) -> _RecordCounts: ...


class CatalogueStatsService:
    """Count what the public catalogue shows, matching the totals of the build and record lists."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[CatalogueStatsUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    @application_operation
    async def stats(self, *, context: ActingContext) -> CatalogueStats:
        async with self._uow_factory() as uow:
            return CatalogueStats(
                builds=await uow.builds.count(statuses=frozenset({BuildStatus.APPROVED})),
                records=await uow.records.count_published_records(filters=ALL_RECORDS),
                creators=await uow.builds.count_creators(),
            )
