"""Commit a bounded batch of search projection changes."""

from typing import Protocol

from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class SearchProjectionRepository(Protocol):
    async def process_batch(self, *, limit: int = 50) -> tuple[int, int]: ...


class SearchProjectionService:
    def __init__(self, *, uow_factory: UnitOfWorkFactory[SearchProjectionUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def process_batch(self, *, limit: int = 50) -> tuple[int, int]:
        async with self._uow_factory() as uow:
            result = await uow.search_projection.process_batch(limit=limit)
            await uow.commit()
            return result


class SearchProjectionUnitOfWork(UnitOfWork, Protocol):
    @property
    def search_projection(self) -> SearchProjectionRepository: ...
