"""Load and persist builds, their relationships, and source-message details."""

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Select, Table, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload, selectinload
from sqlalchemy.orm.attributes import flag_modified, set_committed_value
from sqlalchemy.orm.exc import StaleDataError
from whenever import Instant

from squid.core.accounts.domain import fold_creator_name
from squid.core.accounts.models import Account, CreatorAlias
from squid.core.builds.application.queries import (
    DEFAULT_BUILD_LIST_SORT,
    BuildListSort,
    PublicBuildSummary,
    PublicBuildTag,
)
from squid.core.builds.domain import BuildCategory, BuildStatus, Credit, CreditAttribution, Info, UnresolvedTags
from squid.core.builds.domain.events import BuildEdited
from squid.core.builds.errors import BuildRevisionMismatchError, InvalidBuildError
from squid.core.builds.history import BuildEditActor, BuildEditContext, HistoryOperation
from squid.core.builds.infrastructure.edit_scope import build_edit_scope
from squid.core.builds.infrastructure.loading import BuildLoader
from squid.core.builds.models import (
    BUILD_CLASS_BY_CATEGORY,
    Build,
    BuildCredit,
    BuildLink,
    BuildSourceMessage,
    BuildVersion,
    DoorBuild,
    ExtenderBuild,
)
from squid.core.errors import DataIntegrityError, InvalidStateError, PersistenceError
from squid.core.events.infrastructure.repository import publish_domain_event
from squid.core.messages.domain import MessageSnapshot
from squid.core.messages.models import Message
from squid.core.records.models import DoorTimingVariant, ExtenderTimingVariant
from squid.core.settings.infrastructure.repository import ensure_server_settings
from squid.core.tags.domain import TagSemanticKind, TagValueType
from squid.core.tags.infrastructure.loading import assignment_units, definition_units
from squid.core.tags.models import (
    TagAssignment,
    TagDefinition,
    UnitDefinition,
)
from squid.core.versions.models import Version

logger = logging.getLogger(__name__)


def _page_filter[S: Select[Any]](
    statement: S,
    *,
    statuses: frozenset[BuildStatus],
    submitter_account_id: int | None,
) -> S:
    """Apply the status and submitter visibility policy shared by page and count queries."""
    statement = statement.where(Build.submission_status.in_(statuses))
    if submitter_account_id is not None:
        statement = statement.where(Build.submitter_account_id == submitter_account_id)
    return statement


def _mapper_load_options() -> tuple[Any, ...]:
    """Load tags and links needed to populate a build.

    BuildLoader batches creator and version lookups itself. raiseload prevents
    accidental per-build queries through those relationships.
    """
    return (
        selectinload(Build.tags).options(*assignment_units()),
        selectinload(Build.links),
        raiseload(Build.build_credits),
        raiseload(Build.build_versions),
    )


def _write_load_options() -> tuple[Any, ...]:
    """Load every relationship that _setup_relationships replaces during an update."""
    return (
        selectinload(Build.tags).options(*assignment_units()),
        selectinload(Build.links),
        selectinload(Build.build_credits),
        selectinload(Build.build_versions),
    )


def _public_summary_from_selected_row(
    row: Mapping[str, Any],
    *,
    credits: Sequence[Credit],
    versions: Sequence[str],
    links: Sequence[BuildLink],
    tags: Sequence[PublicBuildTag],
    title_tags: Mapping[str, Sequence[str]],
) -> PublicBuildSummary:
    """Map only the columns selected by the public-summary read model."""
    try:
        category = BuildCategory(row["category"])
        status = BuildStatus(row["submission_status"])
    except (TypeError, ValueError) as exc:
        msg = "A public build has invalid category or status metadata."
        raise DataIntegrityError(msg, context={"build_id": row.get("id")}) from exc

    # The listing selects only the unresolved tag keys from extra_info; titles need nothing else.
    stored = {key: row[key] for key in ("unknown_restrictions", "unknown_patterns") if row[key] is not None}
    try:
        unresolved = UnresolvedTags.read(cast(Info, stored))
    except DataIntegrityError as exc:
        msg = "A public build has malformed unresolved tags."
        raise DataIntegrityError(msg, context={"build_id": row["id"]}) from exc
    extra_info: Info = {}
    unresolved.write(extra_info)

    common: dict[str, Any] = {
        "id": row["id"],
        "revision": row["revision"],
        "submission_status": status,
        "versions": list(versions),
        "version_spec": row["version_spec"],
        "width": row["width"],
        "height": row["height"],
        "depth": row["depth"],
        "patterns": list(title_tags["patterns"]),
        "wiring_placement_restrictions": list(title_tags["wiring-placement"]),
        "animated_restrictions": list(title_tags["animated"]),
        "component_restrictions": list(title_tags["component"]),
        "miscellaneous_restrictions": list(title_tags["miscellaneous"]),
        "extra_info": extra_info,
        "credits": list(credits),
        "links": list(links),
        "display_name": row["display_name"],
        "submission_time": row["submission_time"],
        "edited_time": row["edited_time"],
        "ai_generated": row["ai_generated"],
    }
    if category is BuildCategory.DOOR:
        build = DoorBuild(
            **common,
            orientation=row["door_orientation"],
            expansion=row["door_expansion"],
            door_width=row["door_width"],
            door_height=row["door_height"],
            door_depth=row["door_depth"],
            normal_opening_time=row["normal_opening_time"],
            normal_closing_time=row["normal_closing_time"],
        )
    elif category is BuildCategory.EXTENDER:
        build = ExtenderBuild(
            **common,
            orientation=row["extender_orientation"],
            extension_length=row["extension_length"],
            extender_type=row["extender_type"],
        )
    else:
        build = BUILD_CLASS_BY_CATEGORY[category](**common)
    try:
        return replace(PublicBuildSummary.from_build(build), tags=tuple(tags))
    except InvalidBuildError as exc:
        msg = "A public build cannot be rendered from its persisted category metadata."
        raise DataIntegrityError(msg, context={"build_id": row["id"]}) from exc


class BuildRepository:
    """Load and save build entities within the caller's transaction."""

    __slots__ = ("_loader", "_session")

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._loader = BuildLoader()

    async def get_by_id(self, build_id: int) -> Build | None:
        """Load a build and its related display facts, or return None if missing."""
        session = self._session()
        stmt = select(Build).where(Build.id == build_id).options(*_mapper_load_options())
        result = await session.execute(stmt)
        sql_build = result.unique().scalar_one_or_none()
        if sql_build is None:
            return None
        return await self._loader.load(session, sql_build)

    async def get_for_update(self, build_id: int) -> Build | None:
        """Load a build and hold its row lock until the caller's transaction ends."""
        session = self._session()
        row = await session.scalar(
            select(Build).where(Build.id == build_id).options(*_mapper_load_options()).with_for_update(of=Build.id)
        )
        return None if row is None else await self._loader.load(session, row)

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        """Load builds in requested order, repeating duplicate IDs and omitting missing rows."""
        if not build_ids:
            return []
        session = self._session()
        rows = (
            (await session.scalars(select(Build).where(Build.id.in_(build_ids)).options(*_mapper_load_options())))
            .unique()
            .all()
        )
        by_id = {build.id: build for build in await self._loader.load_many(session, rows)}
        return [by_id[build_id] for build_id in build_ids if build_id in by_id]

    async def get_public_summaries(self, build_ids: Sequence[int]) -> Sequence[PublicBuildSummary]:
        """Return selected public fields for approved builds in requested order.

        This is deliberately not implemented through ``get_many``: record details do not need
        submitter identities, source messages, moderation notes, embeddings, or attachment
        evidence, so those aggregate-only facts never cross this read boundary.
        """
        if not build_ids:
            return ()
        # Core tables keep the joins explicit: subtype ORM columns resolve to their own table, so an ORM
        # projection would still have to name each table to outer-join it rather than cross-join it.
        builds = Build.__table__
        door = DoorBuild.__table__
        extender = ExtenderBuild.__table__
        session = self._session()
        rows = (
            (
                await session.execute(
                    select(
                        builds.c.id.label("id"),
                        builds.c.revision.label("revision"),
                        builds.c.submission_status.label("submission_status"),
                        builds.c.category.label("category"),
                        builds.c.width.label("width"),
                        builds.c.height.label("height"),
                        builds.c.depth.label("depth"),
                        builds.c.display_name.label("display_name"),
                        builds.c.version_spec.label("version_spec"),
                        builds.c.submission_time.label("submission_time"),
                        builds.c.edited_time.label("edited_time"),
                        builds.c.ai_generated.label("ai_generated"),
                        builds.c.extra_info["unknown_restrictions"].label("unknown_restrictions"),
                        builds.c.extra_info["unknown_patterns"].label("unknown_patterns"),
                        door.c.orientation.label("door_orientation"),
                        door.c.expansion.label("door_expansion"),
                        door.c.door_width.label("door_width"),
                        door.c.door_height.label("door_height"),
                        door.c.door_depth.label("door_depth"),
                        door.c.normal_opening_time.label("normal_opening_time"),
                        door.c.normal_closing_time.label("normal_closing_time"),
                        extender.c.orientation.label("extender_orientation"),
                        extender.c.extension_length.label("extension_length"),
                        extender.c.extender_type.label("extender_type"),
                    )
                    .select_from(builds)
                    .outerjoin(door, door.c.build_id == builds.c.id)
                    .outerjoin(extender, extender.c.build_id == builds.c.id)
                    .where(builds.c.id.in_(build_ids), builds.c.submission_status == BuildStatus.APPROVED)
                )
            )
            .mappings()
            .all()
        )
        public_ids = tuple(row["id"] for row in rows)
        if not public_ids:
            return ()

        credits: dict[int, list[Credit]] = defaultdict(list)
        for credit, _, creator_id in await session.execute(BuildLoader.credit_rows(public_ids)):
            credits[credit.build_id].append(credit.to_credit(creator_id))

        versions: dict[int, list[str]] = defaultdict(list)
        for build_id, edition, major, minor, patch in await session.execute(
            select(
                BuildVersion.build_id,
                Version.edition,
                Version.major_version,
                Version.minor_version,
                Version.patch_number,
            )
            .join(Version, Version.id == BuildVersion.version_id)
            .where(BuildVersion.build_id.in_(public_ids))
            .order_by(BuildVersion.build_id, BuildVersion.version_id)
        ):
            versions[build_id].append(f"{edition} {major}.{minor}.{patch}")

        links: dict[int, list[BuildLink]] = defaultdict(list)
        for build_id, url, media_type in await session.execute(
            select(BuildLink.build_id, BuildLink.url, BuildLink.media_type)
            .where(
                BuildLink.build_id.in_(public_ids),
                BuildLink.media_type.in_(("render", "image")),
            )
            .order_by(BuildLink.build_id, BuildLink.url)
        ):
            if media_type in {"render", "image"}:
                links[build_id].append(BuildLink(url=url, media_type=media_type))

        public_tags: dict[int, list[PublicBuildTag]] = defaultdict(list)
        title_tags: dict[int, dict[str, list[str]]] = defaultdict(
            lambda: {
                "patterns": [],
                "wiring-placement": [],
                "animated": [],
                "component": [],
                "miscellaneous": [],
            }
        )
        tag_rows = await session.execute(
            select(TagAssignment)
            .options(*assignment_units())
            .join(TagDefinition, TagDefinition.id == TagAssignment.tag_id)
            .where(TagAssignment.build_id.in_(public_ids))
            .order_by(
                TagAssignment.build_id,
                func.coalesce(TagAssignment.display_order, TagDefinition.default_display_order),
                TagAssignment.tag_id,
            )
        )
        for assignment in tag_rows.scalars():
            tag_row = assignment.definition
            value = assignment.presentation_value
            public_tags[assignment.build_id].append(
                PublicBuildTag(
                    key=tag_row.stable_key,
                    name=tag_row.display_name,
                    value=value,
                    unit=assignment.presentation_unit.key if assignment.presentation_unit else None,
                )
            )
            if tag_row.semantic_kind == TagSemanticKind.PATTERN:
                title_tags[assignment.build_id]["patterns"].append(tag_row.display_name)
            elif tag_row.semantic_kind == TagSemanticKind.RESTRICTION and tag_row.restriction_type is not None:
                title_tags[assignment.build_id][tag_row.restriction_type].append(tag_row.display_name)

        by_id = {
            row["id"]: _public_summary_from_selected_row(
                cast(Mapping[str, Any], row),
                credits=credits[row["id"]],
                versions=versions[row["id"]],
                links=links[row["id"]],
                tags=public_tags[row["id"]],
                title_tags=title_tags[row["id"]],
            )
            for row in rows
        }
        return tuple(by_id[build_id] for build_id in build_ids if build_id in by_id)

    async def get_by_source_submission_draft_id(self, draft_id: uuid.UUID) -> Build | None:
        """Load the build already created from a synchronized submission draft."""
        session = self._session()
        return await self._source(session, draft_id)

    async def _source(self, session: AsyncSession, draft_id: uuid.UUID) -> Build | None:
        """Read a source-draft build within a submission transaction."""
        sql_build = await session.scalar(
            select(Build).where(Build.source_submission_draft_id == draft_id).options(*_mapper_load_options())
        )
        if sql_build is None:
            return None
        return await self._loader.load(session, sql_build)

    async def list_page(
        self,
        *,
        statuses: frozenset[BuildStatus],
        submitter_account_id: int | None = None,
        sort: BuildListSort = DEFAULT_BUILD_LIST_SORT,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
        limit: int,
    ) -> list[Build]:
        """Load a page in display order, filtered by status and optional submitter.

        ID anchors page relative to the display order, so they require the ID sort. A `before_id`
        page is fetched in reversed order and restored in memory; its overfetched row therefore
        sits at the front for the caller to trim.
        """
        if not statuses or limit <= 0:
            return []
        if sort.field != "id" and (after_id is not None or before_id is not None):
            msg = "ID anchors require the ID sort"
            raise ValueError(msg)
        session = self._session()
        statement = _page_filter(
            select(Build),
            statuses=statuses,
            submitter_account_id=submitter_account_id,
        )
        reverse = before_id is not None
        if sort.field == "submission_time":
            time_order = Build.submission_time.desc() if sort.descending else Build.submission_time.asc()
            id_order = Build.id.desc() if sort.descending else Build.id.asc()
            statement = statement.order_by(time_order.nulls_last(), id_order)
        elif before_id is not None:
            # Walk away from the anchor in reversed display order; the page is flipped back below.
            statement = statement.where(Build.id > before_id if sort.descending else Build.id < before_id).order_by(
                Build.id.asc() if sort.descending else Build.id.desc()
            )
        else:
            if after_id is not None:
                statement = statement.where(Build.id < after_id if sort.descending else Build.id > after_id)
            statement = statement.order_by(Build.id.desc() if sort.descending else Build.id.asc())
        if offset:
            statement = statement.offset(offset)
        rows = (await session.scalars(statement.limit(limit).options(*_mapper_load_options()))).unique().all()
        ordered = list(reversed(rows)) if reverse else list(rows)
        return await self._loader.load_many(session, ordered)

    async def count(
        self,
        *,
        statuses: frozenset[BuildStatus],
        submitter_account_id: int | None = None,
    ) -> int:
        """Count the builds a listing can display under a visibility policy."""
        if not statuses:
            return 0
        session = self._session()
        statement = _page_filter(
            select(func.count()).select_from(Build),
            statuses=statuses,
            submitter_account_id=submitter_account_id,
        )
        return await session.scalar(statement) or 0

    async def next_attachment_review(self, after: int) -> int | None:
        builds = Build.__table__.c
        return await self._session().scalar(
            select(builds.id)
            .where(
                builds.id > after,
                builds.submission_status == BuildStatus.APPROVED,
                func.jsonb_typeof(builds.extra_info["attachment_failures"]) == "array",
                builds.extra_info["attachment_failures"][0].is_not(None),
            )
            .order_by(builds.id)
            .limit(1)
        )

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]:
        """Return build IDs linked to the message, in ascending order."""
        session = self._session()
        return (
            await session.scalars(
                select(BuildSourceMessage.build_id)
                .where(BuildSourceMessage.message_id == message_id)
                .order_by(BuildSourceMessage.build_id)
            )
        ).all()

    def editing(
        self,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "edit",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> AbstractAsyncContextManager[None]:
        """Attribute every build mutation in this transaction, including other repositories.

        Open once before writing and commit inside or after the scope. Helpers use
        the existing scope; opening another scope in the transaction is rejected.
        """
        context = BuildEditContext.from_actor(
            actor, source=source, operation=operation, correlation_kind=correlation_kind, correlation_id=correlation_id
        )
        return build_edit_scope(self._session(), context)

    async def save(self, build: Build) -> None:
        """Insert a new build or update an existing build in the caller's transaction.

        A None ID selects insertion. A supplied ID must already exist; this is not
        an upsert. Updates check the revision and reject changes to category,
        existing draft origin, or sponsor attribution.
        """
        build.edited_time = Instant.now()
        build.embedding = None

        # ORM identity columns may still be None before flush.
        if build.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            session = self._session()
            await self._insert(session, build)
            await session.flush()
        else:
            session = self._session()
            await self._update(session, build)
            await publish_domain_event(session, BuildEdited(build.id, build.revision))

    async def change_category(self, current: Build, target: Build) -> int:
        """Replace a locked subtype in the active attributed build-edit transaction."""
        assert current.id == target.id and current.category is not target.category
        session = self._session()
        builds = cast(Table, Build.__table__)
        current_type = cast(Table, type(current).__table__)
        target_type = cast(Table, type(target).__table__)
        # Hold the subtype row against new FK references, then lock any timing
        # variants before taking the historical snapshot that will retain them.
        await session.execute(
            select(current_type.c.build_id).where(current_type.c.build_id == current.id).with_for_update()
        )
        variant_type = (
            DoorTimingVariant
            if isinstance(current, DoorBuild)
            else ExtenderTimingVariant
            if isinstance(current, ExtenderBuild)
            else None
        )
        if variant_type is not None:
            await session.execute(select(variant_type.id).where(variant_type.build_id == current.id).with_for_update())
        await session.execute(select(func.capture_build_edit(current.id)))
        revision = (
            await session.execute(
                update(builds)
                .where(builds.c.id == current.id, builds.c.revision == current.revision)
                .values(
                    category=target.category, revision=builds.c.revision + 1, edited_time=Instant.now(), embedding=None
                )
                .returning(builds.c.revision)
            )
        ).scalar_one_or_none()
        if revision is None:
            raise BuildRevisionMismatchError(current.id, expected_revision=current.revision)
        # The deferred subtype constraint permits the short interval while the
        # parent has its new discriminator but still has the old subtype row.
        await session.execute(delete(current_type).where(current_type.c.build_id == current.id))
        subtype_values = {
            column.name: current.id if column.name == "build_id" else getattr(target, column.name)
            for column in target_type.columns
        }
        await session.execute(target_type.insert().values(subtype_values))
        session.expunge(current)
        await publish_domain_event(session, BuildEdited(current.id, revision))
        return revision

    async def _insert(self, session: AsyncSession, build: Build) -> None:
        """Insert a prepared build within the caller's transaction, including its database events."""
        if build.id is not None:  # pyright: ignore[reportUnnecessaryComparison]
            message = "Transactional insertion requires a new build."
            raise InvalidStateError(message)
        build.edited_time = Instant.now()
        submitter_account_id = await self._resolve_submitter_account_id(session, build)
        build.submitter_account_id = submitter_account_id
        build.submission_status = build.submission_status or BuildStatus.PENDING
        build.ai_generated = build.ai_generated or False
        build.display_name = _normalize_display_name(build.display_name)
        build.submission_time = build.submission_time or Instant.now()
        for name, value in zip(
            (
                "sponsor_installation_id",
                "sponsor_display_name",
                "sponsor_address",
                "sponsor_description",
                "sponsor_website_url",
            ),
            _sponsor_values(build),
            strict=True,
        ):
            setattr(build, name, value)
        sql_build = build
        build.links = list(build.links)
        session.add(sql_build)
        # Relationship lookups issue SELECTs. Delay autoflush until the build and
        # all its assignments are ready to be written together.
        with session.no_autoflush:
            await self._store_source_snapshots(build, session)
            await self._setup_relationships(build, session, sql_build)
        await session.flush()
        await self._sync_source_messages(build, session, store_snapshots=False)

    async def _update(self, session: AsyncSession, build: Build) -> None:
        """Update a prepared build and its events within the caller's transaction."""
        assert build.id is not None
        if build.submission_status is None:
            msg = "Submission status must be set for existing builds."
            raise InvalidStateError(msg, context={"build_id": build.id})
        if build.submitter_account_id is None:
            msg = "Submitter account ID must be set for existing builds."
            raise InvalidStateError(msg, context={"build_id": build.id})

        # Child-only edits must capture the old revision before the ORM advances the root.
        # Otherwise the deferred audit trigger advances it again after edit receipts are written.
        with session.no_autoflush:
            await session.execute(select(func.capture_build_edit(build.id)))

        statement = (
            select(Build).where(Build.id == build.id).options(*_write_load_options()).with_for_update(of=Build.id)
        )
        sql_build = (await session.execute(statement)).scalar_one()
        if sql_build.revision != build.revision:
            raise BuildRevisionMismatchError(
                build.id,
                expected_revision=build.revision,
                current_revision=sql_build.revision,
            )
        submitter_account_id = await self._resolve_submitter_account_id(session, build)
        if sql_build.category != build.category:
            msg = "A persisted build's category cannot be changed."
            raise InvalidBuildError(msg, context={"build_id": build.id})
        if (
            sql_build.source_submission_draft_id is not None
            and build.source_submission_draft_id != sql_build.source_submission_draft_id
        ):
            msg = "A build's source submission draft cannot be changed."
            raise InvalidStateError(msg, context={"build_id": build.id})
        if _sponsor_columns(sql_build) != _sponsor_values(build):
            msg = "A build's sponsor attribution cannot be changed."
            raise InvalidStateError(msg, context={"build_id": build.id})
        build.submitter_account_id = submitter_account_id
        build.display_name = _normalize_display_name(build.display_name)
        build.ai_generated = build.ai_generated or False
        build.edited_time = build.edited_time or Instant.now()
        # A detached edit cannot change the repository lease or the creation timestamp.
        for name in ("locked_at", "lock_token", "lock_expires_at", "submission_time"):
            setattr(build, name, getattr(sql_build, name))
        for name, value in zip(
            (
                "sponsor_installation_id",
                "sponsor_display_name",
                "sponsor_address",
                "sponsor_description",
                "sponsor_website_url",
            ),
            _sponsor_values(build),
            strict=True,
        ):
            setattr(build, name, value)
        try:
            with session.no_autoflush:
                sql_build = await session.merge(build)
                await self._setup_relationships(build, session, sql_build)
                # JSON edits are replacement writes, including nested edits made during preparation.
                sql_build.extra_info = cast(Info, dict(build.extra_info))
                flag_modified(sql_build, "extra_info")
                await self._sync_source_messages(build, session)
            await session.flush()
        except StaleDataError as error:
            raise BuildRevisionMismatchError(build.id, expected_revision=build.revision) from error
        build.revision = sql_build.revision

    async def _resolve_submitter_account_id(self, session: AsyncSession, build: Build) -> int:
        """Require a supplied account ID that already exists."""
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

    async def _setup_relationships(self, build: Build, session: AsyncSession, sql_build: Build) -> None:
        """Rebuild creator, version, and tag relationships from the prepared build."""
        sql_build.build_credits = []
        sql_build.build_versions = []
        legacy = list(
            {
                fold_creator_name(credit.name): credit.name
                for credit in build.credits
                if credit.attribution is CreditAttribution.LEGACY
            }.values()
        )
        aliases = dict(
            zip(
                map(fold_creator_name, legacy),
                await self._get_or_create_aliases(session, legacy),
                strict=True,
            )
        )
        sql_build.build_credits.extend(
            BuildCredit(
                position=position,
                name=credit.name,
                kind=credit.kind,
                username=credit.username,
                subject=credit.subject,
                role=credit.role,
                attribution=credit.attribution,
                source_message_id=credit.source_message_id,
                alias_id=aliases.get(fold_creator_name(credit.name))
                if credit.attribution is CreditAttribution.LEGACY
                else None,
            )
            for position, credit in enumerate(build.credits)
        )

        await self._setup_tag_assignments(build, session, sql_build)

        version_objects = await self._get_versions(session, build.versions)
        sql_build.build_versions.extend(BuildVersion(version_id=version.id) for version in version_objects)

    async def _setup_tag_assignments(
        self,
        build: Build,
        session: AsyncSession,
        sql_build: Build,
    ) -> None:
        """Rebuild assignments against current tag definitions.

        Tag names must already be resolved by apply_build_tags. Reject
        missing definitions and definitions whose value type changed during editing.
        """
        assignments = list(build.tags)
        sql_build.tags = []
        if not assignments:
            return

        definitions = {
            definition.id: definition
            for definition in (
                await session.scalars(
                    select(TagDefinition)
                    .options(*definition_units())
                    .where(TagDefinition.id.in_({assignment.definition.id for assignment in assignments}))
                )
            ).all()
        }
        missing = sorted({assignment.definition.id for assignment in assignments} - definitions.keys())
        if missing:
            msg = f"Unknown tag definition IDs: {missing}"
            raise InvalidBuildError(msg, context={"build_id": build.id, "tag_ids": missing})

        units = {unit.key: unit for unit in await session.scalars(select(UnitDefinition))}

        for assignment in assignments:
            definition = definitions[assignment.definition.id]
            if definition.value_type != assignment.definition.value_type:
                msg = f"Tag {definition.stable_key} value type changed while the build was being edited."
                raise InvalidBuildError(msg, context={"tag_id": definition.id})
            sql_build.tags.append(
                TagAssignment(
                    definition=definition,
                    value=assignment.value,
                    display_unit=(
                        assignment.display_unit
                        if assignment.display_unit in units
                        and definition.canonical_unit_definition is not None
                        and units[assignment.display_unit].dimension == definition.canonical_unit_definition.dimension
                        else None
                    ),
                    display_order=assignment.display_order,
                    evidence=assignment.evidence,
                    origin=assignment.origin,
                    created_by_account_id=build.submitter_account_id,
                )
            )

    @staticmethod
    async def _get_or_create_aliases(session: AsyncSession, igns: list[str]) -> list[int]:
        """Resolve creator credits, creating names not already known.

        Trim names, skip blanks, and deduplicate by normalized spelling while
        preserving input order. The unique constraint and conflict handling let
        concurrent submissions reuse the same alias. The column default derives
        normalized_name from name.
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
        """Resolve canonical version names, raising InvalidBuildError for unknown names."""
        qvn = func.get_quantified_version_names().table_valued("id", "quantified_name").alias("qvn")

        requested = set(version_strings)
        if not requested:
            return []
        # Fetch names and ORM entities together; a tiny set difference gives a
        # useful missing-name error without a second query or an input relation.
        stmt = (
            select(Version, qvn.c.quantified_name)
            .join(qvn, qvn.c.id == Version.id)
            .where(qvn.c.quantified_name.in_(requested))
        )
        matches = (await session.execute(stmt)).all()
        missing = sorted(requested - {name for _, name in matches})
        if missing:
            msg = f"Unknown canonical Minecraft versions: {missing}"
            raise InvalidBuildError(msg, context={"versions": missing})
        return list({version.id: version for version, _ in matches}.values())

    @staticmethod
    async def _store_source_snapshots(build: Build, session: AsyncSession) -> None:
        """Store evidence before inserting credits that reference its message IDs."""
        snapshots = [source for source in build.source_messages if isinstance(source, MessageSnapshot)]
        await ensure_server_settings(session, (snapshot.guild_id for snapshot in snapshots))
        for snapshot in snapshots:
            assert snapshot.author_id is not None, "A source message must record its author."
            await session.execute(
                pg_insert(Message)
                .values(
                    id=snapshot.id,
                    guild_id=snapshot.guild_id,
                    channel_id=snapshot.channel_id,
                    author_id=snapshot.author_id,
                    content=snapshot.content,
                    created_at=snapshot.created_at,
                    observed_at=Instant.now(),
                )
                .on_conflict_do_nothing(index_elements=[Message.id])
            )

    @staticmethod
    async def _sync_source_messages(build: Build, session: AsyncSession, *, store_snapshots: bool = True) -> None:
        """Replace the build's ordered links to its source messages.

        Snapshots, which a new build carries from its draft, are recorded first if
        the message is not stored yet. An existing row is left alone: it may hold a
        later edit or deletion, and other features read it too. Plain references must
        point at stored messages. Write here instead of through MessageService so the
        messages and the build's links commit together.
        """
        assert build.id is not None
        await session.execute(delete(BuildSourceMessage).where(BuildSourceMessage.build_id == build.id))
        if not build.source_messages:
            return

        if store_snapshots:
            await BuildRepository._store_source_snapshots(build, session)

        await session.execute(
            pg_insert(BuildSourceMessage).values(
                [
                    {"build_id": build.id, "message_id": source.id, "position": position}
                    for position, source in enumerate(build.source_messages)
                ]
            )
        )

    async def approve(self, build: Build) -> None:
        """Mark a persisted build approved, checking and advancing its revision."""
        if build.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            msg = "Build ID is missing."
            raise InvalidStateError(msg, context={"operation": "approve"})

        await self._set_status(build, BuildStatus.APPROVED, operation="approve")

    async def reject(self, build: Build) -> None:
        """Mark a persisted build rejected, checking and advancing its revision."""
        if build.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            msg = "Build ID is missing."
            raise InvalidStateError(msg, context={"operation": "reject"})

        await self._set_status(build, BuildStatus.REJECTED, operation="reject")

    async def set_status(self, build: Build, status: BuildStatus) -> None:
        """Write a review status, including PENDING when undoing a decision."""
        await self._set_status(build, status, operation="review")

    async def _set_status(self, build: Build, status: BuildStatus, *, operation: str) -> None:
        assert build.id is not None
        edited_time = Instant.now()
        session = self._session()
        statement = (
            update(Build)
            .where(Build.id == build.id, Build.revision == build.revision)
            .values(
                submission_status=status,
                revision=Build.revision + 1,
                edited_time=edited_time,
            )
        )
        updated = (
            await session.execute(statement.returning(Build.submission_status, Build.revision, Build.edited_time))
        ).one_or_none()
        if updated is None:
            current_revision = await session.scalar(select(Build.revision).where(Build.id == build.id))
            if current_revision is not None:
                raise BuildRevisionMismatchError(
                    build.id, expected_revision=build.revision, current_revision=current_revision
                )
            msg = f"Failed to {operation} submission in the database."
            raise PersistenceError(msg, context={"build_id": build.id, "operation": operation})
        for name, value in zip(("submission_status", "revision", "edited_time"), updated, strict=True):
            set_committed_value(build, name, value)

    async def get_pending(self) -> list[Build]:
        """Return pending builds with the relationships required by the domain mapper."""
        session = self._session()
        statement = select(Build).where(Build.submission_status == BuildStatus.PENDING).options(*_mapper_load_options())
        result = await session.execute(statement)
        return await self._loader.load_many(session, list(result.unique().scalars().all()))

    async def get_builds_by_id(self, build_ids: list[int]) -> list[Build | None]:
        """Return one entry per requested ID, using None for missing builds."""
        if len(build_ids) == 0:
            return []

        session = self._session()
        stmt = select(Build).options(*_mapper_load_options()).where(Build.id.in_(build_ids))
        result = await session.execute(stmt)
        sql_builds = list(result.unique().scalars().all())
        by_id = {build.id: build for build in await self._loader.load_many(session, sql_builds)}
        return [by_id.get(build_id) for build_id in build_ids]


def _split_tag_value(
    assignment: TagAssignment,
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


def _sponsor_columns(build: Build) -> tuple[uuid.UUID | None, str | None, str | None, str | None, str | None]:
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
