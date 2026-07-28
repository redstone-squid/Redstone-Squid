"""SQLAlchemy queries used by build search commands."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.repos._model_repos import (
    _RestrictionAliasModelRepository,
    _RestrictionModelRepository,
    _TypeModelRepository,
)
from squid.db.schema import Restriction, RestrictionAlias, Type
from squid.services.build_queries import RestrictionSearchItem


class BuildMetadataRepository:
    """Query restriction and pattern metadata without exposing sessions to cogs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def search_restrictions(self, query: str | None) -> list[RestrictionSearchItem]:
        async with self._session_factory() as session:
            restriction_repository = _RestrictionModelRepository(session=session)
            alias_repository = _RestrictionAliasModelRepository(session=session)
            restriction_filters = []
            alias_filters = []
            if query:
                restriction_filters.append(Restriction.name.ilike(f"%{query}%"))
                alias_filters.append(RestrictionAlias.alias.ilike(f"%{query}%"))

            restrictions = await restriction_repository.get_many(*restriction_filters)
            aliases = await alias_repository.get_many(*alias_filters)
            return [
                *(RestrictionSearchItem(row.id, row.name, is_alias=False) for row in restrictions),
                *(RestrictionSearchItem(row.restriction_id, row.alias, is_alias=True) for row in aliases),
            ]

    async def list_patterns(self) -> list[str]:
        async with self._session_factory() as session:
            repository = _TypeModelRepository(session=session)
            patterns = await repository.get_many(order_by=(Type.name, False))
            return [pattern.name for pattern in patterns]
