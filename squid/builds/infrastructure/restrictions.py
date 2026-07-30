"""Repository for official restriction metadata."""

from typing import cast

from async_lru import alru_cache
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.application import RestrictionDefinition
from squid.builds.domain import RestrictionTypeLiteral
from squid.builds.errors import AliasAlreadyAddedError, AliasInUseError, RestrictionNotFoundError
from squid.tags.infrastructure.models import TagAlias, TagDefinition


class RestrictionRepository:
    """Convert official tag rows into restriction application values."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        return await self._fetch_all_restrictions()

    @alru_cache
    async def _fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(TagDefinition)
                    .where(
                        TagDefinition.authority == "official",
                        TagDefinition.semantic_kind == "restriction",
                        TagDefinition.moderation_status == "approved",
                    )
                    .order_by(TagDefinition.default_display_order, TagDefinition.display_name)
                )
            ).all()
            return [
                RestrictionDefinition(
                    row.display_name,
                    cast(RestrictionTypeLiteral | None, row.restriction_type),
                )
                for row in rows
            ]

    async def add_alias(self, restriction: str, alias: str) -> None:
        normalized_alias = _normalize(alias)
        async with self._session_factory() as session:
            restriction_id = await get_restriction_id(session, restriction)
            alias_restriction_id = await get_restriction_id(session, alias)
            if restriction_id is None:
                raise RestrictionNotFoundError(restriction)
            if alias_restriction_id == restriction_id:
                raise AliasAlreadyAddedError(alias, restriction_id)
            if alias_restriction_id is not None:
                raise AliasInUseError(alias, alias_restriction_id)

            definition = await session.get(TagDefinition, restriction_id)
            assert definition is not None
            session.add(TagAlias(definition=definition, alias=alias.strip(), normalized_alias=normalized_alias))
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise AliasAlreadyAddedError(alias, restriction_id) from error
            self._fetch_all_restrictions.cache_clear()


async def get_restriction_id(session: AsyncSession, name_or_alias: str) -> int | None:
    normalized = _normalize(name_or_alias)
    statement = (
        select(TagDefinition.id)
        .outerjoin(TagAlias, TagAlias.tag_id == TagDefinition.id)
        .where(
            TagDefinition.authority == "official",
            TagDefinition.semantic_kind == "restriction",
            or_(
                TagDefinition.normalized_name == normalized,
                TagAlias.normalized_alias == normalized,
            ),
        )
    )
    rows = tuple((await session.scalars(statement)).unique().all())
    return rows[0] if len(rows) == 1 else None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
