"""SQLAlchemy queries used by build search commands."""

from rapidfuzz import process
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.application import RestrictionSearchItem
from squid.builds.infrastructure.model_repositories import (
    RestrictionAliasModelRepository,
    RestrictionModelRepository,
    TypeModelRepository,
)
from squid.builds.infrastructure.models import Restriction, RestrictionAlias, Type
from squid.persistence.models import register_models

register_models()


class BuildMetadataRepository:
    """Query restriction and pattern metadata without exposing sessions to cogs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def search_restrictions(self, query: str | None) -> list[RestrictionSearchItem]:
        async with self._session_factory() as session:
            restriction_repository = RestrictionModelRepository(session=session)
            alias_repository = RestrictionAliasModelRepository(session=session)
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
            repository = TypeModelRepository(session=session)
            patterns = await repository.get_many(order_by=(Type.name, False))
            return [pattern.name for pattern in patterns]

    async def search_patterns(self, query: str, limit: int = 25) -> list[tuple[str, float, int]]:
        """Fuzzy search pattern names by substring."""
        patterns = await self.list_patterns()
        return process.extract(query, patterns, limit=limit, score_cutoff=30)
