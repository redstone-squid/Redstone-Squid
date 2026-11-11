"""Build restriction application services."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from async_lru import alru_cache

from squid.builds.domain import RestrictionTypeLiteral
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class RestrictionDefinition:
    """Restriction fields needed when classifying submission input."""

    name: str
    type: RestrictionTypeLiteral | None


class RestrictionRepository(Protocol):
    """Restriction metadata needed by build submission."""

    async def fetch_all_restrictions(self) -> Sequence[RestrictionDefinition]: ...

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

    async def replace_alias(self, restriction: str, alias: str, expected: bool, replacement: bool) -> bool:
        """Conditionally reverse alias membership without touching a different restriction."""
        async with self._uow_factory() as uow:
            changed = await uow.restrictions.replace_alias(restriction, alias, expected, replacement)
            await uow.commit()
        if changed:
            self.fetch_all_restrictions.cache_clear()
        return changed

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
