"""Build restriction application services."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from async_lru import alru_cache

from squid.builds.domain import RestrictionTypeLiteral
from squid.edits.domain import Revision
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class RestrictionDefinition:
    """Restriction fields needed when classifying submission input."""

    name: str
    type: RestrictionTypeLiteral | None


class RestrictionReader(Protocol):
    async def fetch_all_restrictions(self) -> Sequence[RestrictionDefinition]: ...


class RestrictionRepository(RestrictionReader, Protocol):
    """Restriction metadata and alias writes in the caller's transaction."""

    async def alias_revision(self, restriction: str) -> Revision: ...

    async def lock_alias(self, alias: str) -> None: ...

    async def has_alias(self, tag_id: int, alias: str) -> bool: ...

    async def add_alias(self, restriction: str, alias: str) -> None: ...

    async def replace_alias(self, restriction: str, alias: str, expected: bool, replacement: bool) -> bool: ...


class RestrictionService:
    """Application operations for restriction names and aliases."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[RestrictionUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def add_alias(self, restriction: str, alias: str) -> None:
        """Save an alias and invalidate this service's cached restriction list."""
        async with self._uow_factory() as uow:
            await uow.restrictions.add_alias(restriction, alias)
            await uow.commit()
        self.fetch_all_restrictions.cache_clear()

    async def alias_revision(self, restriction: str) -> Revision:
        async with self._uow_factory() as uow:
            return await uow.restrictions.alias_revision(restriction)

    async def names(self) -> list[str]:
        return [restriction.name for restriction in await self.fetch_all_restrictions()]

    @alru_cache
    async def fetch_all_restrictions(self) -> Sequence[RestrictionDefinition]:
        """Return cached approved restriction names and their classifications."""
        async with self._uow_factory() as uow:
            return await uow.restrictions.fetch_all_restrictions()


class RestrictionUnitOfWork(UnitOfWork, Protocol):
    @property
    def restrictions(self) -> RestrictionRepository: ...
