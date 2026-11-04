"""Refresh the search index from queued changes to application resources.

A search document is a searchable copy of a build, record, or metadata resource.
Projection work rebuilds that copy from current data, or removes it when the
resource is deleted or no longer eligible. The service commits document changes
and queue acknowledgements together so a rollback leaves the work available.
"""

from typing import Protocol

from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class SearchProjectionRepository(Protocol):
    """Refresh queued documents in the caller's transaction."""

    async def process_batch(self, *, limit: int = 50) -> tuple[int, int]: ...


class SearchProjectionService:
    """Commit document refreshes and queue updates together."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[SearchProjectionUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def process_batch(self, *, limit: int = 50) -> tuple[int, int]:
        """Process at most limit items and return (succeeded, failed)."""
        async with self._uow_factory() as uow:
            result = await uow.search_projection.process_batch(limit=limit)
            await uow.commit()
            return result


class SearchProjectionUnitOfWork(UnitOfWork, Protocol):
    """Own the transaction used to refresh search documents."""

    @property
    def search_projection(self) -> SearchProjectionRepository: ...
