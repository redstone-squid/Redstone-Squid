"""Read and validate official build taxonomy in short database scopes."""

from collections.abc import Sequence
from typing import Protocol

from async_lru import alru_cache

from squid.builds.application.taxonomy import TaxonomyResolution
from squid.builds.domain import RestrictionTypeLiteral
from squid.tags.models import TagDefinition
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class BuildTaxonomyRepository(Protocol):
    async def get_restriction_id(self, name_or_alias: str) -> int | None: ...
    async def fetch_all_restrictions(self) -> list[TagDefinition]: ...
    async def add_restriction_alias_by_id(self, restriction_id: int, alias: str) -> None: ...
    async def add_restriction_alias(self, name_or_alias: str, alias: str) -> None: ...
    async def get_valid_restrictions(self, type: RestrictionTypeLiteral) -> Sequence[str]: ...
    async def get_valid_door_types(self) -> Sequence[str]: ...
    async def resolve_official(
        self, *, build_kind: str | None, restrictions: Sequence[str], patterns: Sequence[str]
    ) -> TaxonomyResolution: ...


class BuildTaxonomyService:
    """Resolve official names and validate inference inputs."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[TaxonomyUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def get_restriction_id(self, name_or_alias: str) -> int | None:
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.get_restriction_id(name_or_alias)

    @alru_cache
    async def fetch_all_restrictions(self) -> list[TagDefinition]:
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.fetch_all_restrictions()

    async def add_restriction_alias_by_id(self, restriction_id: int, alias: str) -> None:
        async with self._uow_factory() as uow:
            await uow.build_taxonomy.add_restriction_alias_by_id(restriction_id, alias)
            await uow.commit()
        self.fetch_all_restrictions.cache_clear()

    async def add_restriction_alias(self, name_or_alias: str, alias: str) -> None:
        async with self._uow_factory() as uow:
            await uow.build_taxonomy.add_restriction_alias(name_or_alias, alias)
            await uow.commit()
        self.fetch_all_restrictions.cache_clear()

    async def get_valid_restrictions(self, type: RestrictionTypeLiteral) -> Sequence[str]:
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.get_valid_restrictions(type)

    async def get_valid_door_types(self) -> Sequence[str]:
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.get_valid_door_types()

    async def resolve_official(
        self, *, build_kind: str | None, restrictions: Sequence[str], patterns: Sequence[str]
    ) -> TaxonomyResolution:
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.resolve_official(
                build_kind=build_kind, restrictions=restrictions, patterns=patterns
            )

    async def validate_restrictions(
        self,
        restrictions: list[str],
        type: RestrictionTypeLiteral,
    ) -> tuple[list[str], list[str]]:
        valid_by_normalized = {value.casefold(): value for value in await self.get_valid_restrictions(type)}
        valid = [
            valid_by_normalized[value.casefold()] for value in restrictions if value.casefold() in valid_by_normalized
        ]
        invalid = [value for value in restrictions if value.casefold() not in valid_by_normalized]
        return valid, invalid

    async def validate_door_types(self, door_types: list[str]) -> tuple[list[str], list[str]]:
        valid_by_normalized = {value.casefold(): value for value in await self.get_valid_door_types()}
        valid = [
            valid_by_normalized[value.casefold()] for value in door_types if value.casefold() in valid_by_normalized
        ]
        invalid = [value for value in door_types if value.casefold() not in valid_by_normalized]
        return valid, invalid


class TaxonomyUnitOfWork(UnitOfWork, Protocol):
    @property
    def build_taxonomy(self) -> BuildTaxonomyRepository: ...
