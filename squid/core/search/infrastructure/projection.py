"""Refresh search documents from queued changes to builds, records, and metadata.

The loader turns a resource into a SearchProjection: what its hit displays, and
every searchable fact as a field value. A field can have several values, such as
the creators of a build. The store writes the document and its values in one
transaction, and leaves a document whose content has not changed untouched.

The worker processes each queued resource in a savepoint, so one failed refresh
can be retried without losing the other refreshes in the batch. refresh_search_index
commits the batch. Records read their holders' build documents and their tags'
names; a trigger on search_documents queues them when those documents change.
Embedding jobs are queued here but run separately, because their model calls
should not keep this database transaction open.
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import prod

from sqlalchemy import Text, case, cast, delete, func, select
from sqlalchemy.dialects.postgresql import TSVECTOR, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from squid.core.accounts.infrastructure.attribution import attributed_credits, public_account_ids
from squid.core.accounts.models import CreatorAlias
from squid.core.actors import ActingContext
from squid.core.builds.domain.titles import format_build_category
from squid.core.builds.errors import InvalidBuildError
from squid.core.builds.infrastructure.loading import BuildLoader
from squid.core.builds.models import (
    Build,
    BuildCredit,
    BuildVersion,
    DoorBuild,
    ExtenderBuild,
)
from squid.core.diagnostics.log_capture import work_lost
from squid.core.errors import DataIntegrityError
from squid.core.persistence.provenance import bind_work_attribution
from squid.core.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.core.records.application.door_categories import door_category_lookup
from squid.core.records.application.metrics import MetricUnit, snapshot_values
from squid.core.records.application.models import CategoryIdentity
from squid.core.records.domain import BuildKind
from squid.core.records.models import (
    Record,
    RecordDefinition,
    RecordHolder,
    RecordHolderHistory,
    RecordResult,
    RecordTiedBuild,
)
from squid.core.search.domain import ACCOUNT_ID_STORAGE, FieldValue
from squid.core.search.infrastructure.documents import SearchProjection, index_document
from squid.core.search.models import (
    SearchDocument,
    SearchEmbeddingQueueItem,
    SearchProjectionQueueItem,
    SearchTerm,
    SearchValue,
)
from squid.core.tags.domain import TagSemanticKind
from squid.core.tags.infrastructure.loading import assignment_units
from squid.core.tags.models import TagAlias, TagDefinition
from squid.core.tags.models import TagAssignment as TagAssignment
from squid.core.versions.models import Version
from squid.core.worker_context import worker_execution, worker_scope

logger = logging.getLogger(__name__)
PROJECTION_MAX_ATTEMPTS = 5

_BUILD_TIMINGS = (
    ("normal_opening_time", "opening_time"),
    ("visible_opening_time", "visible_opening_time"),
    ("normal_closing_time", "closing_time"),
    ("visible_closing_time", "visible_closing_time"),
    ("extension_time", "extension_time"),
    ("retraction_time", "retraction_time"),
)
"""Build timing columns and the search fields they fill."""

SEARCH_PROJECTION_QUEUE_SPEC = QueueSpec(
    name="search_projections",
    model=SearchProjectionQueueItem,
    key=(SearchProjectionQueueItem.id,),
    available_at=SearchProjectionQueueItem.available_at,
    enqueued_at=SearchProjectionQueueItem.enqueued_at,
    claimed_at=SearchProjectionQueueItem.claimed_at,
    claim_token=SearchProjectionQueueItem.claim_token,
    attempts=SearchProjectionQueueItem.attempts,
    last_error=SearchProjectionQueueItem.last_error,
    dead_at=SearchProjectionQueueItem.dead_at,
)


@dataclass(frozen=True, slots=True)
class RecordProjectionSource:
    """Record fields needed to build its search document.

    A retired record has no result. Its title comes from its latest definition.
    """

    record_id: uuid.UUID
    result_id: int | None
    definition_id: int
    history_complete: bool
    gap_reasons: dict[str, object]
    criterion: str
    build_kind: str
    version_scope: str
    category_key: str
    title: str
    subtitle: str | None
    category_values: tuple[FieldValue, ...] = ()
    former_holder_ids: tuple[int, ...] = ()
    tied_build_ids: tuple[int, ...] = ()

    @property
    def retired(self) -> bool:
        return self.result_id is None


@dataclass(frozen=True, slots=True)
class RecordHolderProjectionSource:
    """Searchable fields from one ranked record holder."""

    build_id: int
    title: str
    metric: dict[str, object]
    creators: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()

    def values(self, build_kind: str) -> tuple[FieldValue, ...]:
        holder = self.build_id
        values = [
            FieldValue("holder", str(holder), holder),
            FieldValue("holder_title", self.title, holder),
            *(FieldValue("holder_creator", name, holder) for name in self.creators),
        ]
        values.extend(
            FieldValue(name, Decimal(value), holder)
            for name, value, unit in snapshot_values(build_kind, self.metric)
            if isinstance(value, int) and unit is not MetricUnit.DATE
        )
        return tuple(values)


@dataclass(frozen=True, slots=True)
class TagProjectionSource:
    """Tag fields needed to build its search document."""

    tag_id: int
    display_name: str
    semantic_kind: str
    moderation_status: str
    authority: str
    value_type: str
    query_name: str | None
    aliases: tuple[str, ...] = ()


class SearchProjectionRepository:
    """Claim projection work and atomically replace indexed documents."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Claims, document updates, and acknowledgements share the caller's
        # transaction so a rollback cannot leave the queue ahead of the index.
        self._queue = ClaimedRowQueue(SEARCH_PROJECTION_QUEUE_SPEC)

    async def claim(self, *, limit: int = 50) -> tuple[SearchProjectionQueueItem, ...]:
        """Lock and mark the oldest available queue items for this worker."""
        return await self._queue.claim(limit=limit, session=self._session)

    async def complete(self, item_id: int, claim_token: uuid.UUID) -> bool:
        """Remove a processed item and return True if its claim token still matches."""
        outcome = await self._queue.complete(
            (SearchProjectionQueueItem.id == item_id,),
            claim_token,
            session=self._session,
        )
        return outcome.applied

    async def retry(
        self,
        item_id: int,
        claim_token: uuid.UUID,
        attempts: int,
        error: Exception,
        *,
        max_attempts: int = PROJECTION_MAX_ATTEMPTS,
    ) -> bool:
        """Schedule another attempt, or retain exhausted work as a dead letter.

        Return True only when this call marks the item as a dead letter. A stale
        claim token leaves the item unchanged.
        """
        if max_attempts < 1:
            msg = "Projection max_attempts must be positive."
            raise ValueError(msg)
        outcome = await self._queue.fail(
            (SearchProjectionQueueItem.id == item_id,),
            claim_token,
            attempts=attempts,
            error=str(error),
            max_attempts=max_attempts,
            session=self._session,
        )
        return outcome.dead_lettered

    def token_of(self, item: SearchProjectionQueueItem) -> uuid.UUID:
        """Return the claim token from a row this store just claimed."""
        return self._queue.token_of(item)

    async def delete_document(self, resource_kind: str, source_key: str) -> None:
        """Deleting the document also removes its indexed field values and embedding job."""
        await self._session.execute(
            delete(SearchDocument).where(
                SearchDocument.resource_kind == resource_kind,
                SearchDocument.source_key == source_key,
            )
        )

    async def replace(self, projection: SearchProjection) -> int:
        """Save a document and its values unless its content is unchanged; return its database ID.

        A changed embedding text clears the old vector, and builds and records then queue
        a new embedding. An embedding job for the same text is left alone, so refreshing
        a document does not reset its claim or retries. The caller owns the transaction.
        """
        indexed = index_document(projection)
        existing = (
            await self._session.execute(
                select(SearchDocument.id, SearchDocument.source_hash).where(
                    SearchDocument.resource_kind == projection.resource_kind,
                    SearchDocument.source_key == projection.source_key,
                )
            )
        ).one_or_none()
        if existing is not None and existing.source_hash == indexed.source_hash:
            return existing.id
        statement = insert(SearchDocument).values(
            resource_kind=projection.resource_kind,
            source_key=projection.source_key,
            title=projection.title,
            subtitle=projection.subtitle,
            description=projection.description,
            status=projection.status,
            normalized_title=indexed.normalized_title,
            heading_terms=list(indexed.heading_terms),
            search_vector=cast(indexed.search_vector, TSVECTOR),
            document_data=projection.document_data,
            source_hash=indexed.source_hash,
            embedding_text=indexed.embedding_text,
            embedding_hash=indexed.embedding_hash,
            refreshed_at=func.now(),
        )
        same_text = SearchDocument.embedding_hash == statement.excluded.embedding_hash
        statement = statement.on_conflict_do_update(
            constraint="search_documents_resource_key",
            set_={
                **{
                    name: statement.excluded[name]
                    for name in (
                        "title",
                        "subtitle",
                        "description",
                        "status",
                        "normalized_title",
                        "heading_terms",
                        "search_vector",
                        "document_data",
                        "source_hash",
                        "embedding_text",
                        "embedding_hash",
                    )
                },
                "embedding": case((same_text, SearchDocument.embedding), else_=None),
                "embedding_model": case((same_text, SearchDocument.embedding_model), else_=None),
                "refreshed_at": func.now(),
            },
        ).returning(SearchDocument.id)
        document_id = (await self._session.execute(statement)).scalar_one()
        await self._session.execute(delete(SearchValue).where(SearchValue.document_id == document_id))
        if indexed.values:
            await self._session.execute(
                insert(SearchValue),
                [
                    {
                        "document_id": document_id,
                        "field_name": value.field,
                        "ordinal": value.ordinal,
                        "holder_build_id": value.holder_build_id,
                        "text_value": value.text,
                        "normalized_text": value.normalized,
                        "term_vector": value.term_vector,
                        "numeric_value": value.numeric,
                        "timestamp_value": value.timestamp,
                        "boolean_value": value.boolean,
                    }
                    for value in indexed.values
                ],
            )
        if indexed.terms:
            await self._session.execute(
                insert(SearchTerm).values([{"term": term} for term in sorted(indexed.terms)]).on_conflict_do_nothing()
            )
        if projection.embedded and indexed.embedding_text:
            embedding_statement = insert(SearchEmbeddingQueueItem).values(
                document_id=document_id,
                source_hash=indexed.embedding_hash,
                enqueued_at=func.now(),
                available_at=func.now(),
                attempts=0,
                claimed_at=None,
                claim_token=None,
                dead_at=None,
                last_error=None,
            )
            await self._session.execute(
                embedding_statement.on_conflict_do_update(
                    index_elements=[SearchEmbeddingQueueItem.document_id],
                    set_={
                        "source_hash": embedding_statement.excluded.source_hash,
                        "enqueued_at": func.now(),
                        "available_at": func.now(),
                        "attempts": 0,
                        "claimed_at": None,
                        "claim_token": None,
                        "dead_at": None,
                        "last_error": None,
                    },
                    where=SearchEmbeddingQueueItem.source_hash != embedding_statement.excluded.source_hash,
                )
            )
        await self._session.flush()
        return document_id


class SearchProjectionLoader:
    """Build search documents from current builds, active records, and approved metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._build_mapper = BuildLoader()

    async def load(self, resource_kind: str, source_key: str) -> SearchProjection | None:
        """Load a searchable resource, or return None for missing, ineligible, or invalid keys."""
        parsed = parse_projection_key(resource_kind, source_key)
        if parsed is None:
            return None
        subtype, source_id = parsed
        if isinstance(source_id, uuid.UUID):
            return await self._record(source_id)
        if resource_kind == "build":
            return await self._build(source_id)
        if resource_kind == "metadata":
            return await self._metadata(subtype, source_id)
        return None

    async def _build(self, build_id: int) -> SearchProjection | None:
        # Gather the resource and related names needed for both display text and filters.
        build = await self._session.scalar(
            select(Build)
            .where(Build.id == build_id)
            .options(
                selectinload(Build.tags).options(*assignment_units()),
                selectinload(Build.tags).joinedload(TagAssignment.definition).selectinload(TagDefinition.aliases),
                selectinload(Build.links),
            )
        )
        if build is None:
            return None
        creators = tuple(
            (
                await self._session.scalars(
                    select(BuildCredit).where(BuildCredit.build_id == build_id).order_by(BuildCredit.position)
                )
            ).all()
        )
        versions = tuple(
            (
                await self._session.scalars(
                    select(Version)
                    .join(BuildVersion, BuildVersion.version_id == Version.id)
                    .where(BuildVersion.build_id == build_id)
                    .order_by(Version.edition, Version.major_version, Version.minor_version, Version.patch_number)
                )
            ).all()
        )
        canonical_title: str | None = None
        canonical_subtitle: str | None = None
        title_diagnostics: list[dict[str, str | list[str]]] = []
        # Keep imperfect builds searchable even when the category formatter cannot describe them.
        try:
            domain_build = await self._build_mapper.load(self._session, build)
            formatted = format_build_category(domain_build)
            # The one-line title adds the restrictions the display title leaves out, so they
            # can match and be highlighted, and results read like catalogue rows.
            title = domain_build.one_line_title
            canonical_title = formatted.title
            canonical_subtitle = formatted.subtitle
            title_diagnostics = [diagnostic.as_dict() for diagnostic in formatted.diagnostics]
        except DataIntegrityError, InvalidBuildError, NotImplementedError, TypeError, ValueError:
            title = f"{build.category or 'Build'} #{build.id}"
        description = build.description
        approved_assignments = tuple(
            assignment for assignment in build.tags if assignment.definition.moderation_status == "approved"
        )
        assigned_tag_names = tuple(assignment.rendered for assignment in approved_assignments)
        restriction_names = tuple(
            assignment.definition.display_name
            for assignment in approved_assignments
            if assignment.definition.authority == "official" and assignment.definition.semantic_kind == "restriction"
        )
        type_names = tuple(
            assignment.definition.display_name
            for assignment in approved_assignments
            if assignment.definition.authority == "official" and assignment.definition.semantic_kind == "pattern"
        )
        creator_names = tuple(name for creator in creators if (name := _mapped_text(creator, "name")) is not None)
        attributed = attributed_credits()
        public_ids = public_account_ids()
        account_ids = tuple(
            str(public_id)
            for public_id in (
                await self._session.scalars(
                    select(public_ids.c.public_id)
                    .join(attributed, attributed.c.account_id == public_ids.c.account_id)
                    .where(attributed.c.build_id == build_id)
                    .distinct()
                    .order_by(public_ids.c.public_id)
                )
            ).all()
        )
        version_names = tuple(_version_name(version) for version in versions)
        dimensions = {
            name: value
            for name, value in (("width", build.width), ("height", build.height), ("depth", build.depth))
            if value is not None
        }
        assert build.submission_status is not None
        assert build.submission_time is not None and build.edited_time is not None
        # The title already ends with the subtitle, so the subtitle is displayed but not indexed again.
        values = [
            FieldValue("title", title),
            *((FieldValue("name", build.display_name),) if build.display_name else ()),
            *((FieldValue("description", description),) if description else ()),
            *build_tag_name_values(approved_assignments),
            *(FieldValue("creator", name) for name in creator_names),
            *(FieldValue(ACCOUNT_ID_STORAGE, public_id) for public_id in account_ids),
            *(FieldValue("version", name) for name in version_names),
            FieldValue("status", build.submission_status.name.lower()),
            FieldValue("kind", build.category or "unknown"),
            *(FieldValue(name, Decimal(value)) for name, value in dimensions.items()),
            *(
                FieldValue(f"tag:{assignment.tag_id}", value)
                for assignment in approved_assignments
                if (value := _assignment_value(assignment)) is not None
            ),
        ]
        if len(dimensions) == 3:
            values.append(FieldValue("volume", Decimal(prod(dimensions.values()))))
        if isinstance(build, DoorBuild):
            values.extend(
                FieldValue(name, Decimal(value))
                for name, value in (
                    ("door_width", build.door_width),
                    ("door_height", build.door_height),
                    ("door_depth", build.door_depth),
                )
                if value is not None
            )
        if isinstance(build, DoorBuild | ExtenderBuild) and build.orientation:
            values.append(FieldValue("orientation", build.orientation))
        if isinstance(build, ExtenderBuild):
            if build.extension_length is not None:
                values.append(FieldValue("extender_length", Decimal(build.extension_length)))
            if build.extender_type:
                values.append(FieldValue("type", build.extender_type))
        for column, name in _BUILD_TIMINGS:
            value = getattr(build, column, None)
            if value is not None:
                values.append(FieldValue(name, Decimal(value)))
        if build.completion_at is not None:
            values.append(FieldValue("completion_at", build.completion_at))
        values.append(FieldValue("created_at", build.submission_time))
        values.append(FieldValue("updated_at", build.edited_time))
        return SearchProjection(
            resource_kind="build",
            source_key=str(build.id),
            title=title,
            subtitle=canonical_subtitle,
            description=description,
            status=build.submission_status.name.lower(),
            document_data={
                "build_id": build.id,
                "canonical_title": canonical_title,
                "canonical_subtitle": canonical_subtitle,
                "title_diagnostics": title_diagnostics,
                "category": build.category,
                "dimensions": dimensions,
                "creators": creator_names,
                "account_ids": account_ids,
                "restrictions": restriction_names,
                "types": type_names,
                "tags": assigned_tag_names,
                "versions": version_names,
            },
            values=tuple(values),
        )

    async def _record(self, record_id: uuid.UUID) -> SearchProjection | None:
        """Load a current or retired record; a record that never had competitors is not searchable."""
        record = await self._session.get(Record, record_id)
        if record is None:
            return None
        result = await self._session.scalar(select(RecordResult).where(RecordResult.record_id == record_id))
        if result is None and record.retired_at is None:
            return None
        definition_query = select(RecordDefinition).where(RecordDefinition.record_id == record_id)
        if result is not None:
            definition_query = definition_query.where(RecordDefinition.id == result.definition_id)
        definition = await self._session.scalar(definition_query.order_by(RecordDefinition.id.desc()).limit(1))
        if definition is None:
            return None
        holders: tuple[RecordHolderProjectionSource, ...] = ()
        tied: tuple[int, ...] = ()
        if result is not None:
            # Holder snapshots carry the record category title. The build document
            # supplies the actual build title and its public credits in one join.
            rows = await self._session.execute(
                select(
                    RecordHolder,
                    SearchDocument.title,
                    SearchDocument.document_data["creators"],
                    SearchDocument.document_data["account_ids"],
                )
                .join(
                    SearchDocument,
                    (SearchDocument.resource_kind == "build")
                    & (SearchDocument.source_key == cast(RecordHolder.build_id, Text)),
                )
                .where(RecordHolder.result_id == result.id, SearchDocument.status == "approved")
                .order_by(RecordHolder.rank, RecordHolder.build_id)
            )
            holders = tuple(
                RecordHolderProjectionSource(
                    build_id=holder.build_id,
                    title=title,
                    creators=tuple(creators or ()),
                    account_ids=tuple(account_ids or ()),
                    metric=holder.metric_snapshot,
                )
                for holder, title, creators, account_ids in rows
            )
            tied = tuple(
                (
                    await self._session.scalars(
                        select(RecordTiedBuild.build_id)
                        .where(RecordTiedBuild.result_id == result.id)
                        .order_by(RecordTiedBuild.build_id)
                    )
                ).all()
            )
        current = {holder.build_id for holder in holders}
        former = tuple(
            build_id
            for build_id in (
                await self._session.scalars(
                    select(RecordHolderHistory.build_id)
                    .where(RecordHolderHistory.record_id == record_id)
                    .distinct()
                    .order_by(RecordHolderHistory.build_id)
                )
            ).all()
            if build_id not in current
        )
        return build_record_projection(
            RecordProjectionSource(
                record_id=record_id,
                result_id=result.id if result is not None else None,
                definition_id=definition.id,
                history_complete=result.history_complete if result is not None else True,
                gap_reasons=result.gap_reasons if result is not None else {},
                criterion=definition.criterion,
                build_kind=definition.build_kind,
                version_scope=definition.version_scope,
                category_key=definition.category_key,
                title=definition.title,
                subtitle=definition.subtitle,
                category_values=await self._record_category_values(definition.category_key),
                former_holder_ids=former,
                tied_build_ids=tied,
            ),
            holders,
        )

    async def _record_category_values(self, key: str) -> tuple[FieldValue, ...]:
        identity = CategoryIdentity.from_key(key)
        if identity is None:
            return ()

        values: list[FieldValue] = []
        pattern_ids: frozenset[int] = frozenset()
        if identity.kind is BuildKind.DOOR and (door := door_category_lookup(identity)) is not None:
            width, height, depth = door.opening
            values.extend(
                (
                    FieldValue("door_width", Decimal(width)),
                    FieldValue("door_height", Decimal(height)),
                    FieldValue("door_depth", Decimal(depth)),
                    FieldValue("orientation", door.orientation),
                )
            )
            pattern_ids = door.type_ids
            if not pattern_ids:
                values.append(FieldValue("type", "Regular"))
        elif identity.kind is BuildKind.EXTENDER:
            parts = identity.base_key.split("|", maxsplit=3)
            if len(parts) == 4 and parts[0] == "extender":
                _, orientation, length, types = parts
                if length.isdecimal():
                    values.extend(
                        (
                            FieldValue("extender_length", Decimal(length)),
                            FieldValue("orientation", orientation),
                        )
                    )
                if types.startswith("t[") and types.endswith("]"):
                    type_key = types[2:-1]
                    if all(part.isdecimal() for part in type_key.split(",")):
                        pattern_ids = frozenset(int(part) for part in type_key.split(","))
                    elif type_key:
                        # Extender categories without pattern tags use the stored type name.
                        values.append(FieldValue("type", type_key))

        restriction_ids = frozenset(identity.restriction_ids)
        tag_ids = pattern_ids | restriction_ids
        if tag_ids:
            values.extend(FieldValue("category_tag_id", Decimal(tag_id)) for tag_id in sorted(tag_ids))
            definitions = await self._session.scalars(
                select(TagDefinition)
                .options(selectinload(TagDefinition.aliases))
                .where(
                    TagDefinition.id.in_(tag_ids),
                    TagDefinition.moderation_status == "approved",
                    TagDefinition.authority == "official",
                )
                .order_by(TagDefinition.id)
            )
            for definition in definitions:
                if definition.id in pattern_ids and definition.semantic_kind == "pattern":
                    field_name = "type"
                elif definition.id in restriction_ids and definition.semantic_kind == "restriction":
                    field_name = "restriction"
                else:
                    continue
                names = {definition.display_name, *(alias.alias for alias in definition.aliases)}
                values.extend(FieldValue(field_name, name) for name in sorted(names))
        return tuple(values)

    async def _metadata(self, subtype: str, source_id: int) -> SearchProjection | None:
        if subtype == "tag":
            definition = await self._session.get(TagDefinition, source_id)
            if definition is None or definition.moderation_status != "approved":
                return None
            aliases = tuple(
                (
                    await self._session.scalars(
                        select(TagAlias.alias).where(TagAlias.tag_id == source_id).order_by(TagAlias.alias)
                    )
                ).all()
            )
            return build_tag_projection(
                TagProjectionSource(
                    tag_id=source_id,
                    display_name=definition.display_name,
                    semantic_kind=TagSemanticKind(definition.semantic_kind).value,
                    moderation_status=definition.moderation_status,
                    authority=definition.authority,
                    value_type=definition.value_type,
                    query_name=definition.query_name,
                    aliases=aliases,
                )
            )
        if subtype == "creator":
            creator = await self._session.get(CreatorAlias, source_id)
            if creator is None:
                return None
            creator_name = _mapped_text(creator, "name")
            if creator_name is None:
                return None
            # The claiming account's identifiers stay out of the public index;
            # only whether the credit has been claimed is searchable.
            return _metadata_projection(
                source_key=f"creator:{source_id}",
                title=creator_name,
                subtype="creator",
                data={"alias_id": source_id, "claimed": creator.account_id is not None},
            )
        if subtype == "version":
            version = await self._session.get(Version, source_id)
            if version is None:
                return None
            title = _version_name(version)
            return _metadata_projection(
                source_key=f"version:{source_id}",
                title=title,
                subtype="version",
                data={"version_id": source_id, "edition": version.edition},
            )
        return None


class SearchProjectionWorker:
    """Process one bounded batch of projection refresh work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = SearchProjectionRepository(session)
        self._loader = SearchProjectionLoader(session)

    async def process_batch(self, *, context: ActingContext, limit: int = 50) -> tuple[int, int]:
        """Process queue items and return successful and failed counts."""
        with worker_scope(context):
            succeeded = 0
            failed = 0
            for item in await self._store.claim(limit=limit):
                # Keep plain values for retry handling. Rolling back the savepoint can
                # expire ORM attributes, whose later access would require an async reload.
                item_id = item.id
                token = self._store.token_of(item)
                attempts = item.attempts
                resource_kind = item.resource_kind
                source_key = item.source_key
                with worker_execution("search-projections", token):
                    await bind_work_attribution(self._session)
                    try:
                        async with self._session.begin_nested():
                            if item.action == "delete":
                                await self._store.delete_document(resource_kind, source_key)
                            else:
                                projection = await self._loader.load(resource_kind, source_key)
                                if projection is None:
                                    await self._store.delete_document(resource_kind, source_key)
                                else:
                                    await self._store.replace(projection)
                            await self._store.complete(item_id, token)
                        succeeded += 1
                    except Exception as error:
                        dead_lettered = await self._store.retry(item_id, token, attempts, error)
                        if dead_lettered:
                            logger.exception(
                                "Dead-lettered a search projection after repeated failures",
                                extra={
                                    "squid.search.resource_kind": resource_kind,
                                    "squid.search.source_key": source_key,
                                    **work_lost(),
                                },
                            )
                        failed += 1
            if succeeded or failed:
                await bind_work_attribution(self._session, reset=True)
            return succeeded, failed


class PostgresSearchProjection:
    """Run projection work in the transaction supplied by the unit of work."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def process_batch(self, *, context: ActingContext, limit: int = 50) -> tuple[int, int]:
        with worker_scope(context):
            return await SearchProjectionWorker(self._session()).process_batch(limit=limit, context=context)


def parse_projection_key(resource_kind: str, source_key: str) -> tuple[str, int | uuid.UUID] | None:
    """Return the subtype and ID encoded by a queue key.

    Builds use a bare ID, records use `record:<public ID>`, and metadata uses
    `subtype:ID`. Return None for an unsupported resource kind or malformed key.
    Metadata subtypes are checked later by the loader.
    """
    if resource_kind == "build" and source_key.isdecimal():
        return "build", int(source_key)
    subtype, separator, raw_id = source_key.partition(":")
    if resource_kind == "record" and separator and subtype == "record":
        try:
            return subtype, uuid.UUID(raw_id)
        except ValueError:
            return None
    if resource_kind == "metadata" and separator and raw_id.isdecimal():
        return subtype, int(raw_id)
    return None


def build_record_projection(
    source: RecordProjectionSource,
    holders: Sequence[RecordHolderProjectionSource] = (),
) -> SearchProjection:
    """Index a record's category, its current holders, and the builds that held or tie it."""
    first_holder = holders[0] if holders else None
    holder_ids = tuple(holder.build_id for holder in holders)
    state = "retired" if source.retired else "current"
    return SearchProjection(
        resource_kind="record",
        source_key=f"record:{source.record_id}",
        title=source.title,
        subtitle=source.subtitle,
        status=state,
        document_data={
            "record_id": str(source.record_id),
            "result_id": source.result_id,
            "definition_id": source.definition_id,
            "base_title": source.criterion,
            "build_kind": source.build_kind,
            "version_scope": source.version_scope,
            "category_key": source.category_key,
            "holders": [
                {"build_id": holder.build_id, "title": holder.title, "creators": holder.creators} for holder in holders
            ],
            "holder_build_ids": holder_ids,
            "build_title": first_holder.title if first_holder is not None else source.title,
            "holder_metrics": {str(holder.build_id): holder.metric for holder in holders},
            "metric": first_holder.metric if first_holder is not None else {},
            "former_holder_ids": source.former_holder_ids,
            "tied_build_ids": source.tied_build_ids,
            "history_complete": source.history_complete,
            "gap_reasons": source.gap_reasons,
        },
        values=(
            FieldValue("title", source.title),
            *((FieldValue("subtitle", source.subtitle),) if source.subtitle else ()),
            FieldValue("base_title", source.criterion),
            FieldValue("record_state", state),
            FieldValue("kind", source.build_kind),
            FieldValue("version_scope", source.version_scope),
            *source.category_values,
            *(FieldValue("creator", name) for name in dict.fromkeys(n for holder in holders for n in holder.creators)),
            *(
                FieldValue(ACCOUNT_ID_STORAGE, public_id)
                for public_id in dict.fromkeys(i for holder in holders for i in holder.account_ids)
            ),
            *(value for holder in holders for value in holder.values(source.build_kind)),
            *(FieldValue("former_holder", Decimal(build_id)) for build_id in source.former_holder_ids),
            *(FieldValue("tied", Decimal(build_id)) for build_id in source.tied_build_ids),
        ),
    )


def build_tag_projection(source: TagProjectionSource) -> SearchProjection | None:
    """Build a tag document, or return None when the tag is not approved."""
    if source.moderation_status != "approved":
        return None
    # The semantic kind makes `kind:pattern` and `kind:restriction` answerable;
    # `kind:tag` remains alongside for clients that used the original tag query.
    return _metadata_projection(
        source_key=f"tag:{source.tag_id}",
        title=source.display_name,
        subtype=source.semantic_kind,
        extra_values=(
            FieldValue("kind", "tag"),
            *(FieldValue(source.semantic_kind, alias) for alias in source.aliases),
            *(FieldValue("tag", name) for name in (source.display_name, *source.aliases)),
        ),
        data={
            "tag_id": source.tag_id,
            "aliases": source.aliases,
            "authority": source.authority,
            "semantic_kind": source.semantic_kind,
            "value_type": source.value_type,
            "query_name": source.query_name,
        },
    )


def _metadata_projection(
    *,
    source_key: str,
    title: str,
    subtype: str,
    extra_values: Sequence[FieldValue] = (),
    data: dict[str, object],
) -> SearchProjection:
    return SearchProjection(
        resource_kind="metadata",
        source_key=source_key,
        title=title,
        document_data={"metadata_kind": subtype, **data},
        values=(
            FieldValue("title", title),
            FieldValue("kind", subtype),
            FieldValue(subtype, title),
            *extra_values,
        ),
    )


def build_tag_name_values(assignments: Sequence[TagAssignment]) -> tuple[FieldValue, ...]:
    """Index assigned names and aliases without changing the displayed tags."""
    values: list[FieldValue] = []
    for assignment in assignments:
        definition = assignment.definition
        names = tuple(dict.fromkeys((definition.display_name, *(alias.alias for alias in definition.aliases))))
        tag_names = tuple(dict.fromkeys((assignment.rendered, *names)))
        values.extend(FieldValue("tag", name) for name in tag_names)
        if definition.authority == "official":
            fields = {"restriction": ("restriction",), "pattern": ("type", "pattern")}.get(definition.semantic_kind, ())
            values.extend(FieldValue(field, name) for field in fields for name in names)
    return tuple(values)


def _version_name(version: Version) -> str:
    return f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"


def _mapped_text(model: object, attribute: str) -> str | None:
    value = vars(model).get(attribute)
    return value if isinstance(value, str) else None


def _assignment_value(assignment: TagAssignment) -> str | Decimal | bool | None:
    value_type = assignment.value_type
    attribute = {
        "numeric": "numeric_value",
        "text": "text_value",
        "boolean": "boolean_value",
    }.get(value_type)
    return None if attribute is None else getattr(assignment, attribute)
