"""Renew or release resource leases, which keep files that undo may restore."""

from typing import Protocol
from uuid import UUID

from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class EditRetentionStore(Protocol):
    async def retain(self, operation_id: UUID, resources: tuple[str, ...]) -> None: ...

    async def renew_retention(self, operations: frozenset[UUID], release: frozenset[UUID]) -> None: ...


class RetentionUnitOfWork(UnitOfWork, Protocol):
    @property
    def edits(self) -> EditRetentionStore: ...


class EditRetentionService:
    def __init__(self, factory: UnitOfWorkFactory[RetentionUnitOfWork]) -> None:
        self._factory = factory

    async def sync(self, retained: frozenset[UUID], released: frozenset[UUID]) -> None:
        """Renew existing live leases; expiration never resurrects deleted resources."""
        if not retained and not released:
            return
        async with self._factory() as uow:
            await uow.edits.renew_retention(retained, released)
            await uow.commit()
