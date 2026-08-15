"""Read-only projections that exist only to answer suggestion queries.

These read the same tables the owning contexts do, but shaped for typeahead: display names
together with every alias a user might type, rather than the normalized entities the owning
services return.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.search.infrastructure.models import SearchDocument
from squid.tags.infrastructure.models import TagApplicability, TagDefinition

TRIGRAM_FLOOR = 0.1
"""Minimum trigram similarity, matching what `PostgresSearchRepository.suggest` already accepts."""


@dataclass(frozen=True, slots=True)
class TaxonomyEntry:
    """One taxonomy value and every string that should match it."""

    stable_key: str
    display_name: str
    semantic_kind: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentEntry:
    """One projected resource offered as a completion."""

    source_key: str
    title: str
    subtitle: str | None
    status: str | None


class PostgresSuggestionRepository:
    """Suggestion-shaped reads over the taxonomy tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def taxonomy(self, semantic_kind: str, *, build_kind: str | None = None) -> Sequence[TaxonomyEntry]:
        """Return approved official definitions of one semantic kind, with their aliases.

        `build_kind` narrows to the definitions declared applicable to that kind of build, which is
        how door types are distinguished from the full pattern list.
        """
        statement = (
            select(TagDefinition)
            .where(
                TagDefinition.authority == "official",
                TagDefinition.semantic_kind == semantic_kind,
                TagDefinition.moderation_status == "approved",
            )
            .order_by(TagDefinition.display_name)
        )
        if build_kind is not None:
            statement = statement.join(TagApplicability, TagApplicability.tag_id == TagDefinition.id).where(
                TagApplicability.build_kind == build_kind
            )
        async with self._session_factory() as session:
            # `TagDefinition.aliases` is a selectin relationship, so aliases arrive without a
            # second round trip and without an N+1 per definition.
            definitions = (await session.scalars(statement)).unique().all()
        return [
            TaxonomyEntry(
                stable_key=row.stable_key,
                display_name=row.display_name,
                semantic_kind=semantic_kind,
                aliases=tuple(alias.alias for alias in row.aliases),
            )
            for row in definitions
        ]

    async def documents(
        self,
        resource_kind: str,
        query: str,
        *,
        statuses: Collection[str] | None = None,
        limit: int,
    ) -> Sequence[DocumentEntry]:
        """Match projected resources by title, riding the existing trigram index.

        Filtering happens here rather than in the shared matcher because there are far more builds
        than an autocomplete can hold in memory, let alone rank per keystroke.
        """
        statement = select(
            SearchDocument.source_key,
            SearchDocument.title,
            SearchDocument.subtitle,
            SearchDocument.status,
        ).where(SearchDocument.resource_kind == resource_kind)
        if statuses is not None:
            statement = statement.where(SearchDocument.status.in_(tuple(statuses)))
        terms = query.strip()
        if terms:
            # An exact identifier match ranks above any title similarity, because pasting the id
            # from an embed is how people reach a specific build and trigram similarity scores a
            # short numeric string against a long title badly.
            exact = SearchDocument.source_key == terms
            similarity = func.similarity(SearchDocument.fuzzy_text, terms)
            statement = statement.where(or_(exact, similarity > TRIGRAM_FLOOR)).order_by(
                exact.desc(), similarity.desc(), SearchDocument.normalized_title
            )
        else:
            # No query yet: offer what changed most recently, which is what a moderator working a
            # queue or a submitter revisiting their build is looking for.
            statement = statement.order_by(SearchDocument.refreshed_at.desc())
        async with self._session_factory() as session:
            rows = (await session.execute(statement.limit(limit))).all()
        return [
            DocumentEntry(source_key=row.source_key, title=row.title, subtitle=row.subtitle, status=row.status)
            for row in rows
        ]
