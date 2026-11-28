"""Read-only projections that exist only to answer suggestion queries.

These read the same tables the owning contexts do, but shaped for typeahead: display names
together with every alias a user might type, rather than the normalized entities the owning
services return.
"""

from collections.abc import Callable, Collection, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from squid.core.accounts.domain import fold_creator_name
from squid.core.accounts.infrastructure.attribution import attributed_credits
from squid.core.accounts.models import Account, AccountProfile, CreatorAlias
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, BuildCredit
from squid.core.records.domain import VersionScope
from squid.core.records.models import Record, RecordDefinition
from squid.core.search.models import SearchDocument, SearchValue
from squid.core.suggestions.application.queries import DocumentEntry, TagEntry
from squid.core.tags.domain import TagValueType
from squid.core.tags.models import TagApplicability, TagDefinition
from squid.core.versions.models import Version

TRIGRAM_FLOOR = 0.1
"""Minimum trigram similarity, matching what `PostgresSearchRepository.suggest` already accepts."""


class PostgresSuggestionRepository:
    """Read tags, search projections, versions, and creators in the caller's session."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def tag_entries(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TagEntry]:
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
            TagEntry(
                id=row.id,
                stable_key=row.stable_key,
                display_name=row.display_name,
                semantic_kind=semantic_kind,
                authority=row.authority,
                aliases=tuple(alias.alias for alias in row.aliases),
                value_type=None if row.value_type is TagValueType.NONE else row.value_type.value,
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
        """Rank exact IDs before trigram matches against the projected title and subtitle.

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
            similarity = func.similarity(SearchDocument.normalized_title, terms)
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

    async def title_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]:
        """Return definition ids with the title an admin recognizes them by.

        Only the all-time scope is offered: every current-scope category has an all-time twin, and
        `/records lookup` materializes the all-time definition regardless of which id it gets.
        """
        statement = (
            select(RecordDefinition.id, RecordDefinition.title, Record.build_kind)
            .join(Record, Record.public_id == RecordDefinition.record_id)
            .where(Record.version_scope == VersionScope.ALL_TIME)
        )
        terms = query.strip()
        if terms:
            condition = RecordDefinition.title.ilike(f"%{terms}%")
            if terms.isdigit():
                condition = or_(condition, RecordDefinition.id == int(terms))
            statement = statement.where(condition)
        statement = statement.order_by(RecordDefinition.title, RecordDefinition.id).limit(limit)
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

    async def conversion_versions(self) -> Sequence[tuple[int, str]]:
        """Return Java releases with a known world-format version."""
        statement = (
            select(Version)
            .where(Version.edition == "Java", Version.data_version.is_not(None))
            .order_by(Version.major_version.desc(), Version.minor_version.desc(), Version.patch_number.desc())
        )
        rows = (await self._session().scalars(statement)).all()
        return [
            (version.data_version, f"Java {version.major_version}.{version.minor_version}.{version.patch_number}")
            for version in rows
            if version.data_version is not None
        ]

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]:
        """Suggest visible profiles by their display name or published credit."""
        terms = fold_creator_name(query)
        owners = attributed_credits()
        credits = (
            select(owners.c.account_id, owners.c.name)
            .join(
                BuildCredit, (BuildCredit.build_id == owners.c.build_id) & (BuildCredit.position == owners.c.position)
            )
            .join(CreatorAlias, CreatorAlias.id == BuildCredit.alias_id)
            .join(Build, Build.id == BuildCredit.build_id)
            .where(
                Build.submission_status == BuildStatus.APPROVED,
                CreatorAlias.normalized_name.startswith(terms, autoescape=True),
            )
        )
        profiles = select(AccountProfile.account_id, AccountProfile.display_name.label("name")).where(
            AccountProfile.hidden.is_(False),
            AccountProfile.display_name.is_not(None),
            func.lower(AccountProfile.display_name).startswith(query.strip().lower(), autoescape=True),
        )
        matches = credits.union_all(profiles).subquery()
        statement = (
            select(Account.public_account_id, func.min(matches.c.name).label("name"))
            .join(matches, matches.c.account_id == Account.id)
            .group_by(Account.public_account_id)
            .order_by("name", Account.public_account_id)
            .limit(limit)
        )
        return [(str(row.public_account_id), row.name) for row in await self._session().execute(statement)]

    async def filter_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]:
        """Return distinct indexed text values for a field, ordered by the database.

        Prefix matching uses `lower(text_value)` to match the expression in the
        functional index. The query is stripped and casefolded before matching.
        """
        statement = select(SearchValue.text_value).where(
            SearchValue.field_name == field_name,
            SearchValue.text_value.is_not(None),
        )
        normalized = prefix.strip().casefold()
        if normalized:
            statement = statement.where(func.lower(SearchValue.text_value).startswith(normalized))
        statement = statement.distinct().order_by(SearchValue.text_value).limit(limit)
        session = self._session()
        return [value for value in (await session.scalars(statement)).all() if value is not None]

    async def record_titles(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]:
        """Return record title UUIDs with the newest definition's readable title.

        The highest definition ID supplies the title and subtitle. Record titles
        without a definition are omitted.
        """
        latest = (
            select(RecordDefinition.record_id, RecordDefinition.title, RecordDefinition.subtitle)
            .distinct(RecordDefinition.record_id)
            .order_by(RecordDefinition.record_id, RecordDefinition.id.desc())
            .subquery()
        )
        statement = select(Record.public_id, latest.c.title, latest.c.subtitle).join(
            latest, latest.c.record_id == Record.public_id
        )
        terms = query.strip()
        if terms:
            statement = statement.where(or_(latest.c.title.ilike(f"%{terms}%"), latest.c.subtitle.ilike(f"%{terms}%")))
        statement = statement.order_by(latest.c.title, Record.public_id).limit(limit)
        session = self._session()
        rows = (await session.execute(statement)).all()
        return [(str(row.public_id), row.title, row.subtitle) for row in rows]

    async def creators(self, query: str, *, limit: int) -> Sequence[tuple[str, bool]]:
        """Return credited creator names and whether each has been claimed by an account."""
        statement = select(CreatorAlias.name).where(
            select(BuildCredit.id)
            .join(Build, Build.id == BuildCredit.build_id)
            .where(BuildCredit.alias_id == CreatorAlias.id, Build.submission_status == BuildStatus.APPROVED)
            .exists()
        )
        # Folded the same way the stored column is, so a prefix typed in any case or
        # compatibility form matches. Anchored on `normalized_name`, which the unique index
        # and the `text_pattern_ops` prefix index are both built on.
        terms = fold_creator_name(query)
        if terms:
            statement = statement.where(CreatorAlias.normalized_name.startswith(terms, autoescape=True))
        statement = statement.order_by(CreatorAlias.normalized_name).limit(limit)
        session = self._session()
        return [(name, False) for name in await session.scalars(statement)]
