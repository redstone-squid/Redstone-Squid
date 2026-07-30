"""SQLAlchemy queries used by build taxonomy commands."""

from rapidfuzz import process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.application import RestrictionSearchItem
from squid.tags.infrastructure.models import TagAlias, TagDefinition


class BuildMetadataRepository:
    """Query restriction and pattern metadata without exposing sessions to cogs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def search_restrictions(self, query: str | None) -> list[RestrictionSearchItem]:
        async with self._session_factory() as session:
            statement = (
                select(TagDefinition)
                .where(
                    TagDefinition.authority == "official",
                    TagDefinition.semantic_kind == "restriction",
                    TagDefinition.moderation_status == "approved",
                )
                .order_by(TagDefinition.display_name)
            )
            alias_statement = (
                select(TagAlias)
                .join(TagDefinition, TagDefinition.id == TagAlias.tag_id)
                .where(
                    TagDefinition.authority == "official",
                    TagDefinition.semantic_kind == "restriction",
                    TagDefinition.moderation_status == "approved",
                )
                .order_by(TagAlias.alias)
            )
            if query:
                statement = statement.where(TagDefinition.display_name.ilike(f"%{query}%"))
                alias_statement = alias_statement.where(TagAlias.alias.ilike(f"%{query}%"))
            restrictions = (await session.scalars(statement)).all()
            aliases = (await session.scalars(alias_statement)).all()
            return [
                *(RestrictionSearchItem(row.id, row.display_name, is_alias=False) for row in restrictions),
                *(RestrictionSearchItem(row.tag_id, row.alias, is_alias=True) for row in aliases),
            ]

    async def list_patterns(self) -> list[str]:
        async with self._session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(TagDefinition.display_name)
                        .where(
                            TagDefinition.authority == "official",
                            TagDefinition.semantic_kind == "pattern",
                            TagDefinition.moderation_status == "approved",
                        )
                        .order_by(TagDefinition.display_name)
                    )
                ).all()
            )

    async def search_patterns(self, query: str, limit: int = 25) -> list[tuple[str, float, int]]:
        """Fuzzy search pattern names by substring."""
        return process.extract(query, await self.list_patterns(), limit=limit, score_cutoff=30)
