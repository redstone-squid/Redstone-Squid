"""Repository for official restriction metadata."""

from collections.abc import Callable
from typing import cast

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from squid.builds.application import RestrictionDefinition
from squid.builds.domain import RestrictionTypeLiteral
from squid.builds.domain.taxonomy import normalize_tag_name
from squid.builds.errors import AliasAlreadyAddedError, AliasInUseError, RestrictionNotFoundError
from squid.tags.models import TagAlias, TagDefinition


class RestrictionRepository:
    """Convert official tag rows into restriction application values."""

    def __init__(self, session: Callable[[], AsyncSession]):
        self._session = session

    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        session = self._session()
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
        """Add a normalized alias in a savepoint; the caller commits the transaction."""
        normalized_alias = normalize_tag_name(alias)
        session = self._session()
        await session.execute(
            select(
                func.pg_advisory_xact_lock(func.hashtextextended("restriction-alias:" + normalize_tag_name(alias), 0))
            )
        )
        restriction_id = await get_restriction_id(session, restriction)
        alias_restriction_id = await get_restriction_id(session, alias)
        if restriction_id is None:
            raise RestrictionNotFoundError(restriction)
        if alias_restriction_id == restriction_id:
            raise AliasAlreadyAddedError(alias, restriction_id)
        if alias_restriction_id is not None:
            raise AliasInUseError(alias, alias_restriction_id)

        definition = await session.get(TagDefinition, restriction_id, with_for_update=True)
        assert definition is not None
        try:
            async with session.begin_nested():
                session.add(TagAlias(definition=definition, alias=alias.strip(), normalized_alias=normalized_alias))
                await session.flush()
        except IntegrityError as error:
            raise AliasAlreadyAddedError(alias, restriction_id) from error

    async def replace_alias(self, restriction: str, alias: str, expected: bool, replacement: bool) -> bool:
        session = self._session()
        await session.execute(
            select(
                func.pg_advisory_xact_lock(func.hashtextextended("restriction-alias:" + normalize_tag_name(alias), 0))
            )
        )
        restriction_id = await get_restriction_id(session, restriction)
        if restriction_id is None:
            return False
        await session.get(TagDefinition, restriction_id, with_for_update=True)
        normalized = normalize_tag_name(alias)
        rows = (
            await session.scalars(select(TagAlias).where(TagAlias.normalized_alias == normalized).with_for_update())
        ).all()
        if any(row.tag_id != restriction_id for row in rows):
            return False
        row = next(iter(rows), None)
        if (row is not None) != expected:
            return False
        if replacement and row is None:
            await self.add_alias(restriction, alias)
        elif not replacement and row is not None:
            await session.delete(row)
        return True


async def get_restriction_id(session: AsyncSession, name_or_alias: str) -> int | None:
    """Resolve an official restriction name or alias to one ID.

    Return None if missing or ambiguous. This lookup does not filter moderation
    status; use fetch_all_restrictions for the approved list.
    """
    normalized = normalize_tag_name(name_or_alias)
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
