"""Persistence and high-level operations for the Build domain object."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import raiseload, selectinload
from sqlalchemy.orm.exc import StaleDataError
from whenever import Instant

from squid.accounts.domain import fold_creator_name
from squid.accounts.infrastructure.models import Account, CreatorAlias
from squid.builds.application.queries import DEFAULT_BUILD_LIST_SORT, BuildListSort, PublicBuildSummary
from squid.builds.domain import (
    Build,
    Status,
)
from squid.builds.errors import BuildRevisionMismatchError, InvalidBuildError
from squid.builds.infrastructure.mapping import SQL_CLASS_BY_CATEGORY, BuildMapper, category_values_from_domain
from squid.builds.infrastructure.models import (
    Build as SQLBuild,
)
from squid.builds.infrastructure.models import (
    BuildCreator,
    BuildLink,
    BuildSourceMessage,
    BuildVersion,
)
from squid.core.errors import InvalidStateError, PersistenceError
from squid.messages.infrastructure.models import Message
from squid.persistence.types import now
from squid.tags.domain import TagAssignment as DomainTagAssignment
from squid.tags.domain import TagValueType
from squid.tags.infrastructure.models import (
    BuildTagAssignment as SQLTagAssignment,
)
from squid.tags.infrastructure.models import (
    TagDefinition as SQLTagDefinition,
)
from squid.versions.infrastructure.models import Version

logger = logging.getLogger(__name__)


def _page_filter[S: Select[Any]](
    statement: S,
    *,
    statuses: frozenset[Status],
    submitter_account_id: int | None,
) -> S:
    """Apply the status and submitter visibility policy shared by page and count queries."""
    statement = statement.where(SQLBuild.submission_status.in_(statuses))
    if submitter_account_id is not None:
        statement = statement.where(SQLBuild.submitter_account_id == submitter_account_id)
    return statement


def _mapper_load_options() -> tuple[Any, ...]:
    """The relationship graph BuildMapper reads off a row.

    Model-level ``lazy="selectin"`` would already load these, but stating the
    contract here keeps every read path loading the same graph rather than
    depending on which query happened to declare it. Creators and versions are
    fenced off because the mapper batches them itself across the whole page;
    ``raiseload`` makes a future traversal fail loudly instead of silently
    reading an empty collection.
    """
    return (
        selectinload(SQLBuild.tag_assignments).selectinload(SQLTagAssignment.definition),
        selectinload(SQLBuild.links),
        raiseload(SQLBuild.build_creators),
        raiseload(SQLBuild.build_versions),
    )


def _write_load_options() -> tuple[Any, ...]:
    """The graph the update path rebuilds, which does traverse every collection."""
    return (
        selectinload(SQLBuild.tag_assignments).selectinload(SQLTagAssignment.definition),
        selectinload(SQLBuild.links),
        selectinload(SQLBuild.build_creators),
        selectinload(SQLBuild.build_versions),
    )


class BuildRepository:
    """Persistence and high-level operations on the Build domain object."""

    __slots__ = ("_mapper", "_session_factory")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._mapper = BuildMapper()

    async def get_by_id(self, build_id: int) -> Build | None:
        """Creates a new Build object from a database ID.

        Args:
            build_id: The ID of the build to retrieve.

        Returns:
            The Build object with the specified ID, or None if the build was not found.
        """
        async with self._session_factory() as session:
            stmt = select(SQLBuild).where(SQLBuild.id == build_id).options(*_mapper_load_options())
            result = await session.execute(stmt)
            sql_build = result.unique().scalar_one_or_none()
            if sql_build is None:
                return None
            return await self._mapper.to_domain(session, sql_build)

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        """Load several builds with one row query and preserve requested ordering."""
        if not build_ids:
            return []
        async with self._session_factory() as session:
            rows = (
                (
                    await session.scalars(
                        select(SQLBuild).where(SQLBuild.id.in_(build_ids)).options(*_mapper_load_options())
                    )
                )
                .unique()
                .all()
            )
            by_id = {build.id: build for build in await self._mapper.to_domain_many(session, rows)}
        return [by_id[build_id] for build_id in build_ids if build_id in by_id]

    async def get_public_summaries(self, build_ids: Sequence[int]) -> Sequence[PublicBuildSummary]:
        """Return allowlisted summaries for confirmed builds in requested order."""
        builds = await self.get_many(build_ids)
        return tuple(
            PublicBuildSummary.from_build(build) for build in builds if build.submission_status is Status.CONFIRMED
        )

    async def get_by_source_submission_draft_id(self, draft_id: uuid.UUID) -> Build | None:
        """Load the build already created from a synchronized submission draft."""
        async with self._session_factory() as session:
            sql_build = await session.scalar(
                select(SQLBuild).where(SQLBuild.source_submission_draft_id == draft_id).options(*_mapper_load_options())
            )
            if sql_build is None:
                return None
            return await self._mapper.to_domain(session, sql_build)

    async def list_page(
        self,
        *,
        statuses: frozenset[Status],
        submitter_account_id: int | None = None,
        sort: BuildListSort = DEFAULT_BUILD_LIST_SORT,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
        limit: int,
    ) -> list[Build]:
        """Load one page of authoritative builds in display order for status and submitter views.

        ID anchors page relative to the display order, so they require the ID sort. A `before_id`
        page is fetched in reversed order and restored in memory; its overfetched row therefore
        sits at the front for the caller to trim.
        """
        if not statuses or limit <= 0:
            return []
        if sort.field != "id" and (after_id is not None or before_id is not None):
            msg = "ID anchors require the ID sort"
            raise ValueError(msg)
        async with self._session_factory() as session:
            statement = _page_filter(
                select(SQLBuild),
                statuses=statuses,
                submitter_account_id=submitter_account_id,
            )
            reverse = before_id is not None
            if sort.field == "submission_time":
                time_order = SQLBuild.submission_time.desc() if sort.descending else SQLBuild.submission_time.asc()
                id_order = SQLBuild.id.desc() if sort.descending else SQLBuild.id.asc()
                statement = statement.order_by(time_order.nulls_last(), id_order)
            elif before_id is not None:
                # Walk away from the anchor in reversed display order; the page is flipped back below.
                statement = statement.where(
                    SQLBuild.id > before_id if sort.descending else SQLBuild.id < before_id
                ).order_by(SQLBuild.id.asc() if sort.descending else SQLBuild.id.desc())
            else:
                if after_id is not None:
                    statement = statement.where(SQLBuild.id < after_id if sort.descending else SQLBuild.id > after_id)
                statement = statement.order_by(SQLBuild.id.desc() if sort.descending else SQLBuild.id.asc())
            if offset:
                statement = statement.offset(offset)
            rows = (await session.scalars(statement.limit(limit).options(*_mapper_load_options()))).unique().all()
            ordered = list(reversed(rows)) if reverse else list(rows)
            return await self._mapper.to_domain_many(session, ordered)

    async def count(
        self,
        *,
        statuses: frozenset[Status],
        submitter_account_id: int | None = None,
    ) -> int:
        """Count the builds a listing can display under a visibility policy."""
        if not statuses:
            return 0
        async with self._session_factory() as session:
            statement = _page_filter(
                # No DISTINCT: the identity join that could multiply rows is gone.
                select(func.count()).select_from(SQLBuild),
                statuses=statuses,
                submitter_account_id=submitter_account_id,
            )
            return await session.scalar(statement) or 0

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]:
        """Return every build inferred from one Discord message.

        Plural because a build-log message routinely yields a bundle, which the old
        single `messages.build_id` column could not express.
        """
        async with self._session_factory() as session:
            return (
                await session.scalars(
                    select(BuildSourceMessage.build_id)
                    .where(BuildSourceMessage.message_id == message_id)
                    .order_by(BuildSourceMessage.build_id)
                )
            ).all()

    async def save(self, build: Build) -> None:
        """
        Updates the build in the database with the given data.

        If the build does not exist in the database, it will be inserted instead.
        """
        build.edited_time = now()

        if build.id is None:
            async with self._session_factory() as session:
                submitter_account_id = await self._resolve_submitter_account_id(session, build)
                sql_build = self._new_model(build, submitter_account_id)
                session.add(sql_build)
                # Mirror _update_existing: taxonomy and version lookups issue SELECTs, and
                # letting them autoflush while tag assignments are wired to a not-yet-added
                # build row raises SAWarning through TagDefinition.assignments.
                with session.no_autoflush:
                    await self._setup_relationships(build, session, sql_build)
                await session.flush()
                build.id = sql_build.id
                await self._sync_source_messages(build, session)
                await session.commit()
                build.revision = sql_build.revision
        else:
            await self._update_existing(build)

    async def _update_existing(self, build: Build) -> None:
        """Persist an existing build while its repository lease is held."""
        assert build.id is not None
        if build.submission_status is None:
            msg = "Submission status must be set for existing builds."
            raise InvalidStateError(msg, context={"build_id": build.id})
        if build.submitter_account_id is None:
            msg = "Submitter account ID must be set for existing builds."
            raise InvalidStateError(msg, context={"build_id": build.id})

        async with self._session_factory() as session:
            statement = select(SQLBuild).where(SQLBuild.id == build.id).options(*_write_load_options())
            sql_build = (await session.execute(statement)).scalar_one()
            if sql_build.revision != build.revision:
                raise BuildRevisionMismatchError(
                    build.id,
                    expected_revision=build.revision,
                    current_revision=sql_build.revision,
                )
            submitter_account_id = await self._resolve_submitter_account_id(session, build)
            try:
                sql_build.submission_status = build.submission_status
                sql_build.width = build.width
                sql_build.height = build.height
                sql_build.depth = build.depth
                sql_build.completion_time = build.completion_time
                sql_build.completion_at = build.completion_at
                sql_build.completion_evidence = build.completion_evidence
                sql_build.description = build.description
                sql_build.display_name = _normalize_display_name(build.display_name)
                if (
                    sql_build.source_submission_draft_id is not None
                    and build.source_submission_draft_id != sql_build.source_submission_draft_id
                ):
                    msg = "A build's source submission draft cannot be changed."
                    raise InvalidStateError(msg, context={"build_id": build.id})
                sql_build.source_submission_draft_id = build.source_submission_draft_id
                if _sponsor_columns(sql_build) != _sponsor_values(build):
                    msg = "A build's sponsor attribution cannot be changed."
                    raise InvalidStateError(msg, context={"build_id": build.id})
                sql_build.submitter_account_id = submitter_account_id
                sql_build.version_spec = build.version_spec
                sql_build.ai_generated = build.ai_generated or False
                sql_build.embedding = build.embedding
                # Never None: `save` stamps it before dispatching here, and the column is NOT NULL.
                sql_build.edited_time = build.edited_time if build.edited_time is not None else now()

                self._update_category_fields(build, sql_build)

                # Taxonomy and version lookups issue SELECTs. Keep them from autoflushing a
                # half-rebuilt graph, which would both expose an intermediate state to hooks
                # and consume more than one optimistic revision for this logical edit.
                with session.no_autoflush:
                    sql_build.build_creators.clear()
                    sql_build.build_versions.clear()
                    sql_build.tag_assignments.clear()
                    sql_build.links.clear()
                    await self._setup_relationships(build, session, sql_build)
                    sql_build.extra_info = build.extra_info
                await self._sync_source_messages(build, session)
                await session.commit()
            except StaleDataError as error:
                await session.rollback()
                raise BuildRevisionMismatchError(build.id, expected_revision=build.revision) from error
            build.revision = sql_build.revision

    @staticmethod
    def _new_model(build: Build, submitter_account_id: int) -> SQLBuild:
        """Construct the joined row matching the domain category."""
        common: dict[str, Any] = {
            "submission_status": build.submission_status or Status.PENDING,
            "record_category": build.record_category,
            "width": build.width,
            "height": build.height,
            "depth": build.depth,
            "completion_time": build.completion_time,
            "completion_at": build.completion_at,
            "completion_evidence": build.completion_evidence,
            "description": build.description,
            "display_name": _normalize_display_name(build.display_name),
            "source_submission_draft_id": build.source_submission_draft_id,
            "sponsor_installation_id": None if build.sponsor is None else build.sponsor.installation_id,
            "sponsor_display_name": None if build.sponsor is None else build.sponsor.display_name,
            "sponsor_address": None if build.sponsor is None else build.sponsor.address,
            "sponsor_description": None if build.sponsor is None else build.sponsor.description,
            "sponsor_website_url": None if build.sponsor is None else build.sponsor.website_url,
            "category": build.category,
            "submitter_account_id": submitter_account_id,
            "version_spec": build.version_spec,
            "ai_generated": build.ai_generated or False,
            "embedding": build.embedding,
            "extra_info": build.extra_info,
            "edited_time": build.edited_time,
            "is_locked": False,
        }
        return SQL_CLASS_BY_CATEGORY[build.category](**common, **category_values_from_domain(build))

    @staticmethod
    def _update_category_fields(build: Build, sql_build: SQLBuild) -> None:
        """Update facts owned by one joined category without allowing a category switch."""
        if sql_build.category != build.category:
            msg = "A persisted build's category cannot be changed."
            raise InvalidBuildError(
                msg,
                context={"build_id": build.id, "current_category": sql_build.category, "category": build.category},
            )
        for name, value in category_values_from_domain(build).items():
            setattr(sql_build, name, value)

    async def _resolve_submitter_account_id(self, session: AsyncSession, build: Build) -> int:
        """Check that the supplied owning account exists.

        This used to mint an account from a snowflake when none was supplied -- the last
        identity-creating path outside the accounts context, reached from a persistence
        layer with no evidence anybody had asked to be remembered. Callers now resolve an
        account before submitting.
        """
        if build.submitter_account_id is None:
            msg = "Submitter account ID must be set for new builds."
            raise InvalidStateError(msg, context={"resource": "build"})
        account_exists = await session.scalar(select(Account.id).where(Account.id == build.submitter_account_id))
        if account_exists is None:
            msg = "Submitter account does not exist."
            raise InvalidStateError(
                msg,
                context={"resource": "build", "submitter_account_id": build.submitter_account_id},
            )
        return build.submitter_account_id

    async def _setup_relationships(self, build: Build, session: AsyncSession, sql_build: SQLBuild) -> None:
        """Set up all relationships for the build using SQLAlchemy's relationship handling."""
        # Handle creators
        if build.creators_ign:
            alias_ids = await self._get_or_create_aliases(session, build.creators_ign)
            sql_build.build_creators.extend(BuildCreator(alias_id=alias_id) for alias_id in alias_ids)

        await self._setup_tag_assignments(build, session, sql_build)

        # Handle versions
        version_objects = await self._get_versions(session, build.versions)
        sql_build.build_versions.extend(BuildVersion(version_id=version.id) for version in version_objects)

        # Handle links
        sql_build.links.extend(BuildLink(url=link.url, media_type=link.media_type) for link in build.links)

    async def _setup_tag_assignments(
        self,
        build: Build,
        session: AsyncSession,
        sql_build: SQLBuild,
    ) -> None:
        """Persist ``build.tags`` verbatim.

        Taxonomy names are resolved into assignments at edit time by
        ``squid.builds.application.taxonomy.apply_build_taxonomy``; the
        repository no longer interprets the restriction string fields.
        """
        assignments = build.tags
        if not assignments:
            return

        definitions = {
            definition.id: definition
            for definition in (
                await session.scalars(
                    select(SQLTagDefinition).where(
                        SQLTagDefinition.id.in_({assignment.definition.id for assignment in assignments})
                    )
                )
            ).all()
        }
        missing = sorted({assignment.definition.id for assignment in assignments} - definitions.keys())
        if missing:
            msg = f"Unknown tag definition IDs: {missing}"
            raise InvalidBuildError(msg, context={"build_id": build.id, "tag_ids": missing})

        for assignment in assignments:
            definition = definitions[assignment.definition.id]
            if definition.value_type != assignment.definition.value_type:
                msg = f"Tag {definition.stable_key} value type changed while the build was being edited."
                raise InvalidBuildError(msg, context={"tag_id": definition.id})
            numeric_value, text_value, boolean_value = _split_tag_value(assignment)
            sql_build.tag_assignments.append(
                SQLTagAssignment(
                    definition=definition,
                    value_type=assignment.definition.value_type,
                    numeric_value=numeric_value,
                    text_value=text_value,
                    boolean_value=boolean_value,
                    display_unit_key=assignment.display_unit,
                    display_order=assignment.display_order,
                    evidence=assignment.evidence,
                    provenance=assignment.provenance,
                    created_by_account_id=build.submitter_account_id,
                )
            )

    @staticmethod
    async def _get_or_create_aliases(session: AsyncSession, igns: list[str]) -> list[int]:
        """Return the creator alias IDs for *igns*, creating missing names.

        Names are matched case-insensitively via the ``normalized_name``
        column, so ``Foo`` and ``foo`` share one credit. The insert relies on
        that column's unique constraint rather than a read-then-write, so two
        submissions naming the same creator cannot race. The column is written
        from ``name`` by a column default, so it is never set here.
        """
        alias_ids: list[int] = []
        seen: set[str] = set()
        for ign in igns:
            name = ign.strip()
            folded = fold_creator_name(name)
            if not folded or folded in seen:
                continue
            seen.add(folded)

            alias_id = await session.scalar(select(CreatorAlias.id).where(CreatorAlias.normalized_name == folded))
            if alias_id is None:
                result = await session.execute(
                    pg_insert(CreatorAlias)
                    .values(name=name)
                    .on_conflict_do_nothing(index_elements=[CreatorAlias.normalized_name])
                    .returning(CreatorAlias.id)
                )
                alias_id = result.scalar_one_or_none()
            if alias_id is None:
                # Lost the insert race against a concurrent submission naming the same creator.
                alias_id = (
                    await session.execute(select(CreatorAlias.id).where(CreatorAlias.normalized_name == folded))
                ).scalar_one()
            alias_ids.append(alias_id)

        return alias_ids

    @staticmethod
    async def _get_versions(session: AsyncSession, version_strings: list[str]) -> list[Version]:
        """Get Version objects for the given version strings."""
        qvn = func.get_quantified_version_names().table_valued("id", "quantified_name").alias("qvn")

        requested = set(version_strings)
        stmt = select(qvn.c.id, qvn.c.quantified_name).where(qvn.c.quantified_name.in_(requested))
        result = await session.execute(stmt)
        matches = result.all()
        found = {row.quantified_name for row in matches}
        missing = sorted(requested - found)
        if missing:
            msg = f"Unknown canonical Minecraft versions: {missing}"
            raise InvalidBuildError(msg, context={"versions": missing})
        version_ids = [row.id for row in matches]

        stmt = select(Version).where(Version.id.in_(version_ids))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def _sync_source_messages(build: Build, session: AsyncSession) -> None:
        """Record the message facts a build came from, then relink the build to them.

        The message rows are upserted rather than inserted because the same Discord
        message legitimately backs several builds: a build-log post that yields a
        bundle is one message fact with one link row per build.

        This writes `messages` directly instead of going through `MessageService`
        because `build_source_messages.message_id` is RESTRICT: the fact and the link
        have to land in one transaction, or the link has a window where its target
        does not exist yet.
        """
        assert build.id is not None
        await session.execute(delete(BuildSourceMessage).where(BuildSourceMessage.build_id == build.id))
        if not build.source_messages:
            return

        for source in build.source_messages:
            assert source.author_id is not None, "A source message must record its author."
            await session.execute(
                pg_insert(Message)
                .values(
                    id=source.message_id,
                    guild_id=source.guild_id,
                    channel_id=source.channel_id,
                    author_id=source.author_id,
                    content=source.content,
                    observed_at=Instant.now(),
                )
                .on_conflict_do_update(
                    index_elements=[Message.id],
                    set_={
                        "guild_id": source.guild_id,
                        "channel_id": source.channel_id,
                        "author_id": source.author_id,
                        "content": source.content,
                    },
                )
            )

        await session.execute(
            pg_insert(BuildSourceMessage).values(
                [
                    {"build_id": build.id, "message_id": source.message_id, "position": position}
                    for position, source in enumerate(build.source_messages)
                ]
            )
        )

    async def confirm(self, build: Build) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        if build.id is None:
            msg = "Build ID is missing."
            raise InvalidStateError(msg, context={"operation": "confirm"})

        await self._set_status(build, Status.CONFIRMED, operation="confirm")

    async def deny(self, build: Build) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        if build.id is None:
            msg = "Build ID is missing."
            raise InvalidStateError(msg, context={"operation": "deny"})

        await self._set_status(build, Status.DENIED, operation="deny")

    async def _set_status(self, build: Build, status: Status, *, operation: str) -> None:
        assert build.id is not None
        edited_time = now()
        async with self._session_factory() as session:
            statement = (
                update(SQLBuild)
                .where(SQLBuild.id == build.id, SQLBuild.revision == build.revision)
                .values(
                    submission_status=status,
                    revision=SQLBuild.revision + 1,
                    edited_time=edited_time,
                )
            )
            result = cast(CursorResult[Any], await session.execute(statement))
            if result.rowcount != 1:
                current_revision = await session.scalar(select(SQLBuild.revision).where(SQLBuild.id == build.id))
                await session.rollback()
                if current_revision is not None:
                    raise BuildRevisionMismatchError(
                        build.id,
                        expected_revision=build.revision,
                        current_revision=current_revision,
                    )
                msg = f"Failed to {operation} submission in the database."
                raise PersistenceError(msg, context={"build_id": build.id, "operation": operation})
            await session.commit()
        build.submission_status = status
        build.revision += 1
        build.edited_time = edited_time

    async def get_pending(self) -> list[Build]:
        """Return pending builds with the relationships required by the domain mapper."""
        async with self._session_factory() as session:
            statement = (
                select(SQLBuild).where(SQLBuild.submission_status == Status.PENDING).options(*_mapper_load_options())
            )
            result = await session.execute(statement)
            return await self._mapper.to_domain_many(session, list(result.unique().scalars().all()))

    async def get_builds_by_id(self, build_ids: list[int]) -> list[Build | None]:
        """Fetches builds from the database with the given IDs."""
        if len(build_ids) == 0:
            return []

        async with self._session_factory() as session:
            stmt = select(SQLBuild).options(*_mapper_load_options()).where(SQLBuild.id.in_(build_ids))
            result = await session.execute(stmt)
            sql_builds = list(result.unique().scalars().all())
            by_id = {build.id: build for build in await self._mapper.to_domain_many(session, sql_builds)}
            return [by_id.get(build_id) for build_id in build_ids]


def _split_tag_value(
    assignment: DomainTagAssignment,
) -> tuple[Decimal | None, str | None, bool | None]:
    value_type = assignment.definition.value_type
    value = assignment.value
    if value_type is TagValueType.NONE and value is None:
        return None, None, None
    if value_type is TagValueType.NUMERIC and isinstance(value, Decimal):
        return value, None, None
    if value_type is TagValueType.TEXT and isinstance(value, str):
        return None, value, None
    if value_type is TagValueType.BOOLEAN and isinstance(value, bool):
        return None, None, value
    msg = f"Tag {assignment.definition.stable_key} expects a {value_type.value} value."
    raise InvalidBuildError(msg, context={"tag_id": assignment.definition.id})


def _normalize_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 120:
        msg = "Build display names cannot exceed 120 characters."
        raise InvalidBuildError(msg, context={"length": len(normalized)})
    return normalized


def _sponsor_columns(build: SQLBuild) -> tuple[uuid.UUID | None, str | None, str | None, str | None, str | None]:
    return (
        build.sponsor_installation_id,
        build.sponsor_display_name,
        build.sponsor_address,
        build.sponsor_description,
        build.sponsor_website_url,
    )


def _sponsor_values(build: Build) -> tuple[uuid.UUID | None, str | None, str | None, str | None, str | None]:
    if build.sponsor is None:
        return (None, None, None, None, None)
    return (
        build.sponsor.installation_id,
        build.sponsor.display_name,
        build.sponsor.address,
        build.sponsor.description,
        build.sponsor.website_url,
    )
