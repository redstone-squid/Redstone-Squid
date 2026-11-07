"""Read-only projections that exist only to answer suggestion queries.

These read the same tables the owning contexts do, but shaped for typeahead: display names
together with every alias a user might type, rather than the normalized entities the owning
services return.
"""

from collections.abc import Callable, Collection, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from squid.accounts.domain import fold_creator_name
from squid.accounts.models import Account, CreatorAlias
from squid.records.domain import VersionScope
from squid.records.models import RecordTitle, TitleDefinition
from squid.search.models import SearchDocument, SearchDocumentFacet
from squid.suggestions.application.queries import DocumentEntry, TaxonomyEntry
from squid.tags.models import TagApplicability, TagDefinition
from squid.versions.models import Version

TRIGRAM_FLOOR = 0.1
"""Minimum trigram similarity, matching what `PostgresSearchRepository.suggest` already accepts."""


class PostgresSuggestionRepository:
    """Read taxonomy, search projections, versions, and creators in the caller's session."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def taxonomy(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TaxonomyEntry]:
        """Return approved definitions of one semantic kind, with their aliases.

        `build_kind` limits results to definitions declared applicable to that kind
        of build. `authority=None` accepts every authority, as needed for showcase tags.
        """
        statement = (
            select(TagDefinition)
            .options(selectinload(TagDefinition.aliases))
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
        session = self._session()
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
        """Rank exact IDs before trigram matches against projected search text.

        Empty queries return the most recently refreshed rows. Status filtering
        is optional; the caller must supply it when a source excludes private builds.
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
        session = self._session()
        rows = (await session.execute(statement.limit(limit))).all()
        return [
            DocumentEntry(source_key=row.source_key, title=row.title, subtitle=row.subtitle, status=row.status)
            for row in rows
        ]

    async def record_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]:
        """Return definition ids with the title an admin recognizes them by.

        Only the all-time scope is offered: every current-scope category has an all-time twin, and
        `/records lookup` materializes the all-time definition regardless of which id it gets.
        """
        statement = (
            select(TitleDefinition.id, TitleDefinition.title, RecordTitle.build_kind)
            .join(RecordTitle, RecordTitle.public_id == TitleDefinition.competition_id)
            .where(RecordTitle.version_scope == VersionScope.ALL_TIME)
        )
        terms = query.strip()
        if terms:
            condition = TitleDefinition.title.ilike(f"%{terms}%")
            if terms.isdigit():
                condition = or_(condition, TitleDefinition.id == int(terms))
            statement = statement.where(condition)
        statement = statement.order_by(TitleDefinition.title, TitleDefinition.id).limit(limit)
        session = self._session()
        return [(row.id, row.title, row.build_kind) for row in (await session.execute(statement)).all()]

    async def version_ids(self) -> Sequence[tuple[int, str]]:
        """Return version IDs and names, ordered by edition and then newest release first."""
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
        session = self._session()
        rows = (await session.execute(statement)).all()
        return [(row.id, f"{row.edition} {row.major_version}.{row.minor_version}.{row.patch_number}") for row in rows]

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]:
        """Return public UUIDs for accounts with claimed aliases.

        Each account is labelled by its alphabetically first alias. A nonempty
        query matches the first normalized alias, not every alias on the account.
        """
        statement = (
            select(Account.public_creator_id, func.min(CreatorAlias.name).label("name"))
            .join(CreatorAlias, CreatorAlias.account_id == Account.id)
            .group_by(Account.public_creator_id)
        )
        terms = fold_creator_name(query)
        if terms:
            statement = statement.having(func.min(CreatorAlias.normalized_name).startswith(terms))
        statement = statement.order_by("name").limit(limit)
        session = self._session()
        rows = (await session.execute(statement)).all()
        return [(str(row.public_creator_id), row.name) for row in rows]

    async def facet_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]:
        """Return distinct non-null facet values in database text order.

        Prefix matching uses `lower(text_value)` to match the expression in the
        functional index. The query is stripped and casefolded before matching.
        """
        statement = select(SearchDocumentFacet.text_value).where(
            SearchDocumentFacet.field_name == field_name,
            SearchDocumentFacet.text_value.is_not(None),
        )
        normalized = prefix.strip().casefold()
        if normalized:
            statement = statement.where(func.lower(SearchDocumentFacet.text_value).startswith(normalized))
        statement = statement.distinct().order_by(SearchDocumentFacet.text_value).limit(limit)
        session = self._session()
        return [value for value in (await session.scalars(statement)).all() if value is not None]

    async def competitions(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]:
        """Return record competition UUIDs with the newest definition's readable title.

        The highest definition ID supplies the title and subtitle. Competitions
        without a definition are omitted.
        """
        latest = (
            select(TitleDefinition.competition_id, TitleDefinition.title, TitleDefinition.subtitle)
            .distinct(TitleDefinition.competition_id)
            .order_by(TitleDefinition.competition_id, TitleDefinition.id.desc())
            .subquery()
        )
        statement = select(RecordTitle.public_id, latest.c.title, latest.c.subtitle).join(
            latest, latest.c.competition_id == RecordTitle.public_id
        )
        terms = query.strip()
        if terms:
            statement = statement.where(or_(latest.c.title.ilike(f"%{terms}%"), latest.c.subtitle.ilike(f"%{terms}%")))
        statement = statement.order_by(latest.c.title, RecordTitle.public_id).limit(limit)
        session = self._session()
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
        session = self._session()
        return [(row.name, row.account_id is not None) for row in (await session.execute(statement)).all()]
