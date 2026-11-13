"""Read and validate official build taxonomy in short database scopes."""

import json
from collections.abc import Sequence
from typing import Protocol

from async_lru import alru_cache

from squid.builds.application.taxonomy import TaxonomyResolution
from squid.builds.domain import RestrictionTypeLiteral, normalize_tag_name
from squid.core.concurrency import run_all_awaitables
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
    async def showcase_definitions(self, keys: Sequence[str]) -> Sequence[TagDefinition]: ...


class BuildTaxonomyService:
    """Resolve official names and validate inference inputs."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[TaxonomyUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def get_restriction_id(self, name_or_alias: str) -> int | None:
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.get_restriction_id(name_or_alias)

    @alru_cache
    async def fetch_all_restrictions(self) -> list[TagDefinition]:
        """Return cached approved restriction definitions and their aliases."""
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

    async def inference_vocabulary(self) -> str:
        """Return the current approved names, grouped by the inference fields."""
        types: tuple[RestrictionTypeLiteral, ...] = ("component", "wiring-placement", "animated", "miscellaneous")
        results = await run_all_awaitables(
            [*(self.get_valid_restrictions(type) for type in types), self.get_valid_door_types()]
        )
        return json.dumps(dict(zip((*types, "patterns"), results, strict=True)))

    async def resolve_official(
        self, *, build_kind: str | None, restrictions: Sequence[str], patterns: Sequence[str]
    ) -> TaxonomyResolution:
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.resolve_official(
                build_kind=build_kind, restrictions=restrictions, patterns=patterns
            )

    async def showcase_definitions(self, keys: Sequence[str]) -> Sequence[TagDefinition]:
        if not keys:
            return ()
        async with self._uow_factory() as uow:
            return await uow.build_taxonomy.showcase_definitions(keys)

    async def validate_restrictions(
        self,
        restrictions: list[str],
        type: RestrictionTypeLiteral,
    ) -> tuple[list[str], list[str]]:
        """Resolve approved names and aliases, retaining unknown or ambiguous input."""
        resolution = await self.resolve_official(build_kind=None, restrictions=restrictions, patterns=())
        return _validated_names(
            restrictions,
            [item.definition for item in resolution.assignments if item.definition.restriction_type == type],
            resolution.unknown_restrictions,
        )

    async def validate_door_types(self, door_types: list[str]) -> tuple[list[str], list[str]]:
        """Split door pattern names into recognized display names and unrecognized input."""
        resolution = await self.resolve_official(build_kind="Door", restrictions=(), patterns=door_types)
        return _validated_names(
            door_types, [item.definition for item in resolution.assignments], resolution.unknown_patterns
        )


def _validated_names(
    requested: list[str], definitions: list[TagDefinition], unresolved: frozenset[str]
) -> tuple[list[str], list[str]]:
    by_name = {
        name: definition.display_name
        for definition in definitions
        for name in {definition.normalized_name, *(alias.normalized_alias for alias in definition.aliases)}
    }
    recognized: list[str] = []
    unknown: list[str] = []
    for value in requested:
        name = normalize_tag_name(value)
        if name in unresolved or name not in by_name:
            unknown.append(value)
        elif by_name[name] not in recognized:
            recognized.append(by_name[name])
    return recognized, unknown


class TaxonomyUnitOfWork(UnitOfWork, Protocol):
    @property
    def build_taxonomy(self) -> BuildTaxonomyRepository: ...
