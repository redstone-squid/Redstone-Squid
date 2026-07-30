"""Validation adapter for the unified build taxonomy."""

from collections.abc import Sequence

from async_lru import alru_cache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.domain.models import RestrictionTypeLiteral
from squid.builds.errors import AliasAlreadyAddedError, AliasInUseError, RestrictionNotFoundError
from squid.builds.infrastructure.restrictions import RestrictionRepository, get_restriction_id
from squid.tags.infrastructure.models import TagDefinition


class BuildTagsManager:
    """Validate inferred restriction and pattern names."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self.session = session

    async def get_restriction_id(self, name_or_alias: str) -> int | None:
        async with self.session() as session:
            return await get_restriction_id(session, name_or_alias)

    @alru_cache
    async def fetch_all_restrictions(self) -> list[TagDefinition]:
        async with self.session() as session:
            result = await session.execute(
                select(TagDefinition).where(
                    TagDefinition.authority == "official",
                    TagDefinition.semantic_kind == "restriction",
                    TagDefinition.moderation_status == "approved",
                )
            )
            return list(result.scalars().all())

    async def add_restriction_alias_by_id(self, restriction_id: int, alias: str) -> None:
        async with self.session() as session:
            definition = await session.get(TagDefinition, restriction_id)
        if definition is None:
            raise RestrictionNotFoundError(str(restriction_id))
        await RestrictionRepository(self.session).add_alias(definition.display_name, alias)
        self.fetch_all_restrictions.cache_clear()

    async def add_restriction_alias(self, name_or_alias: str, alias: str) -> None:
        rid = await self.get_restriction_id(name_or_alias)
        alias_rid = await self.get_restriction_id(alias)
        if rid is None:
            raise RestrictionNotFoundError(name_or_alias)
        if alias_rid == rid:
            raise AliasAlreadyAddedError(alias, rid)
        if alias_rid is not None:
            raise AliasInUseError(alias, alias_rid)
        await self.add_restriction_alias_by_id(rid, alias)

    async def get_valid_restrictions(self, type: RestrictionTypeLiteral) -> Sequence[str]:
        async with self.session() as session:
            return (
                await session.scalars(
                    select(TagDefinition.display_name).where(
                        TagDefinition.authority == "official",
                        TagDefinition.semantic_kind == "restriction",
                        TagDefinition.restriction_type == type,
                        TagDefinition.moderation_status == "approved",
                    )
                )
            ).all()

    async def get_valid_door_types(self) -> Sequence[str]:
        async with self.session() as session:
            return (
                await session.scalars(
                    select(TagDefinition.display_name).where(
                        TagDefinition.authority == "official",
                        TagDefinition.semantic_kind == "pattern",
                        TagDefinition.moderation_status == "approved",
                        TagDefinition.applicabilities.any(build_kind="Door"),
                    )
                )
            ).all()

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
