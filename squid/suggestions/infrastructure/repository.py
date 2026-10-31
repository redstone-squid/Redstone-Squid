"""Read-only projections that exist only to answer suggestion queries.

These read the same tables the owning contexts do, but shaped for typeahead: display names
together with every alias a user might type, rather than the normalized entities the owning
services return.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import fold_creator_name
from squid.accounts.infrastructure.models import Account, CreatorAlias
from squid.records.domain import VersionScope
from squid.records.infrastructure.models import RecordCompetition, RecordDefinition
from squid.search.infrastructure.models import SearchDocument, SearchDocumentFacet
from squid.tags.infrastructure.models import TagApplicability, TagDefinition
from squid.versions.infrastructure.models import Version

TRIGRAM_FLOOR = 0.1
"""Minimum trigram similarity, matching what `PostgresSearchRepository.suggest` already accepts."""


@dataclass(frozen=True, slots=True)
class TaxonomyEntry:
    """One taxonomy value and every string that should match it."""

    id: int
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

    async def taxonomy(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TaxonomyEntry]:
        """Return approved definitions of one semantic kind, with their aliases.

        `build_kind` narrows to the definitions declared applicable to that kind of build, which is
        how door types are distinguished from the full pattern list. `authority` is `None` for
        showcase tags, which users propose and so are never `official`.
        """
        statement = (
            select(TagDefinition)
            .where(
                TagDefinition.semantic_kind == semantic_kind,
                TagDefinition.moderation_status == "approved",
            )
            .order_by(TagDefinition.display_name)
        )
        if authority is not None:
            statement = statement.where(TagDefinition.authority == authority)
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
                id=row.id,
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

    async def record_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]:
        """Return definition ids with the title an admin recognizes them by.

        Only the all-time scope is offered: every current-scope category has an all-time twin, and
        `/admin records-lookup` materializes the all-time definition regardless of which id it gets.
        """
        statement = select(RecordDefinition.id, RecordDefinition.title, RecordDefinition.build_kind).where(
            RecordDefinition.version_scope == VersionScope.ALL_TIME.value
        )
        terms = query.strip()
        if terms:
            condition = RecordDefinition.title.ilike(f"%{terms}%")
            if terms.isdigit():
                condition = or_(condition, RecordDefinition.id == int(terms))
            statement = statement.where(condition)
        statement = statement.order_by(RecordDefinition.title, RecordDefinition.id).limit(limit)
        async with self._session_factory() as session:
            return [(row.id, row.title, row.build_kind) for row in (await session.execute(statement)).all()]

    async def version_ids(self) -> Sequence[tuple[int, str]]:
        """Return each recognized version's database id with its display name.

        The domain `MinecraftVersion` carries no id, but the commands that pin a record to a
        release take one, which is the whole reason those parameters read as raw numbers today.
        """
        statement = select(
            Version.id,
            Version.edition,
            Version.major_version,
            Version.minor_version,
            Version.patch_number,
        ).order_by(
            Version.edition,
            Version.major_version.desc(),
            Version.minor_version.desc(),
            Version.patch_number.desc(),
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [(row.id, f"{row.edition} {row.major_version}.{row.minor_version}.{row.patch_number}") for row in rows]

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]:
        """Return public creator UUIDs with a name to recognize them by.

        A creator is an account with at least one claimed alias, which is the same population
        `subscription_target_exists` validates a creator subscription against.
        """
        statement = (
            select(Account.public_creator_id, func.min(CreatorAlias.name).label("name"))
            .join(CreatorAlias, CreatorAlias.account_id == Account.id)
            .group_by(Account.public_creator_id)
        )
        terms = query.strip().casefold()
        if terms:
            statement = statement.having(func.min(CreatorAlias.normalized_name).startswith(terms))
        statement = statement.order_by("name").limit(limit)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [(str(row.public_creator_id), row.name) for row in rows]

    async def facet_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]:
        """Return distinct indexed values of one projected facet, prefix-matched.

        Matching is case-folded against `lower(text_value)` so it can ride the functional index
        added for exactly this query; an `ILIKE` here would fall back to a sequential scan over
        every facet row in the corpus.
        """
        statement = select(SearchDocumentFacet.text_value).where(
            SearchDocumentFacet.field_name == field_name,
            SearchDocumentFacet.text_value.is_not(None),
        )
        normalized = prefix.strip().casefold()
        if normalized:
            statement = statement.where(func.lower(SearchDocumentFacet.text_value).startswith(normalized))
        statement = statement.distinct().order_by(SearchDocumentFacet.text_value).limit(limit)
        async with self._session_factory() as session:
            return [value for value in (await session.scalars(statement)).all() if value is not None]

    async def competitions(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]:
        """Return record competition UUIDs with the newest definition's readable title.

        Competitions store no title of their own, so this borrows the newest definition's; every
        competition has at least one definition because both are only ever created together.
        """
        latest = (
            select(RecordDefinition.competition_id, RecordDefinition.title, RecordDefinition.subtitle)
            .distinct(RecordDefinition.competition_id)
            .order_by(RecordDefinition.competition_id, RecordDefinition.id.desc())
            .subquery()
        )
        statement = select(RecordCompetition.public_id, latest.c.title, latest.c.subtitle).join(
            latest, latest.c.competition_id == RecordCompetition.public_id
        )
        terms = query.strip()
        if terms:
            statement = statement.where(or_(latest.c.title.ilike(f"%{terms}%"), latest.c.subtitle.ilike(f"%{terms}%")))
        statement = statement.order_by(latest.c.title, RecordCompetition.public_id).limit(limit)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [(str(row.public_id), row.title, row.subtitle) for row in rows]

    async def creators(self, query: str, *, limit: int) -> Sequence[tuple[str, bool]]:
        """Return credited creator names and whether each has been claimed by an account."""
        statement = select(CreatorAlias.name, CreatorAlias.account_id)
        # Folded the same way the stored column is, so a prefix typed in any case or
        # compatibility form matches. Anchored on `normalized_name`, which the unique index
        # and the `text_pattern_ops` prefix index are both built on.
        terms = fold_creator_name(query)
        if terms:
            statement = statement.where(CreatorAlias.normalized_name.startswith(terms, autoescape=True))
        statement = statement.order_by(CreatorAlias.normalized_name).limit(limit)
        async with self._session_factory() as session:
            return [(row.name, row.account_id is not None) for row in (await session.execute(statement)).all()]
