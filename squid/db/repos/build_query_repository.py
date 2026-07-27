"""SQLAlchemy queries used by build search commands."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import Restriction, RestrictionAlias, Type
from squid.services.build_queries import RestrictionSearchItem


class BuildMetadataRepository:
    """Query restriction and pattern metadata without exposing sessions to cogs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def search_restrictions(self, query: str | None) -> list[RestrictionSearchItem]:
        async with self._session_factory() as session:
            restriction_stmt = select(Restriction)
            alias_stmt = select(RestrictionAlias)
            if query:
                restriction_stmt = restriction_stmt.where(Restriction.name.ilike(f"%{query}%"))
                alias_stmt = alias_stmt.where(RestrictionAlias.alias.ilike(f"%{query}%"))

            restriction_result, alias_result = await asyncio.gather(
                session.execute(restriction_stmt),
                session.execute(alias_stmt),
            )
            return [
                *(
                    RestrictionSearchItem(row.id, row.name, is_alias=False)
                    for row in restriction_result.scalars().all()
                ),
                *(
                    RestrictionSearchItem(row.restriction_id, row.alias, is_alias=True)
                    for row in alias_result.scalars().all()
                ),
            ]

    async def list_patterns(self) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(select(Type.name).order_by(Type.name))
            return list(result.scalars().all())
