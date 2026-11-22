"""Refresh search documents from queued changes to builds, records, and metadata.

The loader assembles the display text, result data, and typed values used by
structured filters. The store replaces the document and its indexed field values
in one transaction. A field can have multiple values, such as the creators of a build.

The worker processes each queued resource in a savepoint, so one failed refresh
can be retried without losing the other refreshes in the batch. refresh_search_index
commits the batch. Embedding jobs are queued here but run separately, because
their model calls should not keep this database transaction open.
"""

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from math import prod

from sqlalchemy import Text, case, cast, delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from whenever import Instant

from squid.core.accounts.infrastructure.attribution import attributed_credits, public_creator_ids
from squid.core.accounts.models import CreatorAlias
from squid.core.builds.domain.titles import format_build_category
from squid.core.builds.errors import InvalidBuildError
from squid.core.builds.infrastructure.loading import BuildLoader
from squid.core.builds.models import (
    Build,
    BuildCredit,
    BuildVersion,
    DoorBuild,
)
from squid.core.diagnostics.log_capture import work_lost
from squid.core.errors import DataIntegrityError
from squid.core.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.core.records.application.door_categories import door_category_lookup
from squid.core.records.application.models import CategoryIdentity
from squid.core.records.domain import BuildKind
from squid.core.records.models import (
    ComputationRun,
    RecordDefinition,
    RecordHolder,
    RecordResult,
)
from squid.core.search.domain import CREATOR_ID_STORAGE
from squid.core.search.infrastructure.holder_metrics import timing_values
from squid.core.search.models import (
    SearchDocument,
    SearchDocumentFilter,
    SearchEmbeddingQueueItem,
    SearchProjectionQueueItem,
)
from squid.core.tags.domain import TagSemanticKind
from squid.core.tags.infrastructure.loading import assignment_units
from squid.core.tags.models import TagAlias, TagDefinition
from squid.core.tags.models import TagAssignment as TagAssignment
from squid.core.versions.models import Version

logger = logging.getLogger(__name__)
PROJECTION_MAX_ATTEMPTS = 5

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
class ProjectionFilter:
    """One typed filter value emitted by a search projection."""

    field_name: str
    value: str | Decimal | Instant | bool
    holder_build_id: int | None = None


@dataclass(frozen=True, slots=True)
class SearchProjection:
    """The complete replacement state of one search document."""

    resource_kind: str
    source_key: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()
    search_aliases: tuple[str, ...] = ()
    document_data: dict[str, object] = field(default_factory=dict)
    filters: tuple[ProjectionFilter, ...] = ()


@dataclass(frozen=True, slots=True)
class RecordProjectionSource:
    """Record fields needed to build its search document."""

    result_id: int
    definition_id: int
    status: str
    history_complete: bool
    gap_reasons: dict[str, object]
    criterion: str
    build_kind: str
    version_scope: str
    category_key: str
    title: str
    subtitle: str | None
    category_filters: tuple[ProjectionFilter, ...] = ()


@dataclass(frozen=True, slots=True)
class RecordHolderProjectionSource:
    """Searchable fields from one ranked record holder."""

    build_id: int
    title: str
    metric: dict[str, object]
    creators: tuple[str, ...] = ()
    creator_ids: tuple[str, ...] = ()

    def filters(self, build_kind: str) -> tuple[ProjectionFilter, ...]:
        filters = [ProjectionFilter("holder", str(self.build_id), self.build_id)]
        volume = self.metric.get("volume")
        if type(volume) is int:
            filters.append(ProjectionFilter("volume", Decimal(volume), self.build_id))
        filters.extend(
            ProjectionFilter(name, Decimal(value), self.build_id)
            for name, value in timing_values(build_kind, self.metric.get("timing"))
        )
        return tuple(filters)


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
        """Save a document and its indexed field values, then return its database ID.

        A changed source hash clears the old embedding. Builds and records also
        enqueue embedding work. An existing queue item with the same hash is left
        alone so that refreshing a projection does not reset its claim or retries.
        The caller owns the transaction.
        """
        normalized_title = normalize_search_text(projection.title)
        tags = sorted({normalize_search_text(tag) for tag in projection.tags if tag.strip()})
        search_terms = sorted(
            {*tags, *(normalize_search_text(alias) for alias in projection.search_aliases if alias.strip())}
        )
        fuzzy_text = " ".join(
            part for part in (normalized_title, normalize_search_text(projection.subtitle or ""), *search_terms) if part
        )
        source_hash = projection_source_hash(projection)
        title_vector = func.to_tsvector(literal_column("'simple'"), func.unaccent(projection.title))
        description_vector = func.to_tsvector(
            literal_column("'simple'"),
            func.unaccent(projection.description or ""),
        )
        tag_text = " ".join(search_terms)
        combined_vector = (
            func.setweight(title_vector, literal_column("'A'"))
            .op("||")(
                func.setweight(
                    func.to_tsvector(literal_column("'simple'"), func.unaccent(tag_text)),
                    literal_column("'A'"),
                )
            )
            .op("||")(
                func.setweight(
                    func.to_tsvector(
                        literal_column("'simple'"),
                        func.unaccent(projection.subtitle or ""),
                    ),
                    literal_column("'B'"),
                )
            )
            .op("||")(func.setweight(description_vector, literal_column("'C'")))
        )
        statement = insert(SearchDocument).values(
            resource_kind=projection.resource_kind,
            source_key=projection.source_key,
            title=projection.title,
            subtitle=projection.subtitle,
            description=projection.description,
            status=projection.status,
            normalized_title=func.unaccent(normalized_title),
            fuzzy_text=func.unaccent(fuzzy_text),
            tags=tags,
            title_vector=title_vector,
            description_vector=description_vector,
            combined_vector=combined_vector,
            document_data=projection.document_data,
            source_hash=source_hash,
            refreshed_at=func.now(),
        )
        statement = statement.on_conflict_do_update(
            constraint="search_documents_resource_key",
            set_={
                "title": statement.excluded.title,
                "subtitle": statement.excluded.subtitle,
                "description": statement.excluded.description,
                "status": statement.excluded.status,
                "normalized_title": statement.excluded.normalized_title,
                "fuzzy_text": statement.excluded.fuzzy_text,
                "tags": statement.excluded.tags,
                "title_vector": statement.excluded.title_vector,
                "description_vector": statement.excluded.description_vector,
                "combined_vector": statement.excluded.combined_vector,
                "document_data": statement.excluded.document_data,
                "source_hash": statement.excluded.source_hash,
                "embedding": case(
                    (SearchDocument.source_hash == statement.excluded.source_hash, SearchDocument.embedding),
                    else_=None,
                ),
                "embedding_model": case(
                    (SearchDocument.source_hash == statement.excluded.source_hash, SearchDocument.embedding_model),
                    else_=None,
                ),
                "refreshed_at": func.now(),
            },
        ).returning(SearchDocument.id)
        document_id = (await self._session.execute(statement)).scalar_one()
        await self._session.execute(delete(SearchDocumentFilter).where(SearchDocumentFilter.document_id == document_id))
        self._session.add_all(build_filter_models(document_id, projection.filters))
        if projection.resource_kind in {"build", "record"}:
            embedding_statement = insert(SearchEmbeddingQueueItem).values(
                document_id=document_id,
                source_hash=source_hash,
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
        if resource_kind == "build":
            return await self._build(source_id)
        if resource_kind == "record":
            return await self._computed_record(source_id)
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
        search_aliases = tuple(
            sorted({alias.alias for assignment in approved_assignments for alias in assignment.definition.aliases})
        )
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
        public_ids = public_creator_ids()
        creator_ids = tuple(
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
        # Index typed values for structured filters. The text tags above support lexical matching.
        filters = [
            *build_tag_name_filters(approved_assignments),
            *(ProjectionFilter("creator", name) for name in creator_names),
            *(ProjectionFilter(CREATOR_ID_STORAGE, public_id) for public_id in creator_ids),
            *(ProjectionFilter("version", name) for name in version_names),
            ProjectionFilter("status", build.submission_status.name.lower()),
            ProjectionFilter("kind", build.category or "unknown"),
            *(ProjectionFilter(name, Decimal(value)) for name, value in dimensions.items()),
            *(
                ProjectionFilter(f"tag:{assignment.tag_id}", value)
                for assignment in approved_assignments
                if (value := _assignment_value(assignment)) is not None
            ),
        ]
        if len(dimensions) == 3:
            filters.append(ProjectionFilter("volume", Decimal(prod(dimensions.values()))))
        if isinstance(build, DoorBuild):
            filters.extend(
                ProjectionFilter(name, Decimal(value))
                for name, value in (
                    ("door_width", build.door_width),
                    ("door_height", build.door_height),
                    ("door_depth", build.door_depth),
                )
                if value is not None
            )
        for name in (
            "normal_opening_time",
            "visible_opening_time",
            "normal_closing_time",
            "visible_closing_time",
        ):
            value = getattr(build, name, None)
            if value is not None:
                filters.append(ProjectionFilter(name.removeprefix("normal_"), Decimal(value)))
        if build.completion_at is not None:
            filters.append(ProjectionFilter("completion_at", build.completion_at))
        filters.append(ProjectionFilter("created_at", build.submission_time))
        filters.append(ProjectionFilter("updated_at", build.edited_time))
        return SearchProjection(
            resource_kind="build",
            source_key=str(build.id),
            title=title,
            subtitle=canonical_subtitle,
            description=description,
            status=build.submission_status.name.lower(),
            tags=(*assigned_tag_names, *creator_names, *version_names),
            search_aliases=search_aliases,
            document_data={
                "build_id": build.id,
                "canonical_title": canonical_title,
                "canonical_subtitle": canonical_subtitle,
                "title_diagnostics": title_diagnostics,
                "category": build.category,
                "dimensions": dimensions,
                "creators": creator_names,
                "creator_ids": creator_ids,
                "restrictions": restriction_names,
                "types": type_names,
                "tags": assigned_tag_names,
                "versions": version_names,
            },
            filters=tuple(filters),
        )

    async def _computed_record(self, result_id: int) -> SearchProjection | None:
        row = (
            await self._session.execute(
                select(RecordResult, RecordDefinition)
                .options(selectinload(RecordDefinition.record))
                .join(ComputationRun, ComputationRun.id == RecordResult.run_id)
                .join(RecordDefinition, RecordDefinition.id == RecordResult.definition_id)
                .where(RecordResult.id == result_id, ComputationRun.is_active.is_(True))
            )
        ).one_or_none()
        if row is None:
            return None
        result, definition = row
        # Holder snapshots carry the record category title. The build document
        # supplies the actual build title and its public credits in one join.
        holders = await self._session.execute(
            select(
                RecordHolder,
                SearchDocument.title,
                SearchDocument.document_data["creators"],
                SearchDocument.document_data["creator_ids"],
            )
            .join(
                SearchDocument,
                (SearchDocument.resource_kind == "build")
                & (SearchDocument.source_key == cast(RecordHolder.build_id, Text)),
            )
            .where(RecordHolder.result_id == result_id, SearchDocument.status == "approved")
            .order_by(RecordHolder.rank, RecordHolder.build_id)
        )
        return build_record_projection(
            RecordProjectionSource(
                result_id=result.id,
                definition_id=definition.id,
                status=result.status,
                history_complete=result.history_complete,
                gap_reasons=result.gap_reasons,
                criterion=definition.criterion,
                build_kind=definition.build_kind,
                version_scope=definition.version_scope,
                category_key=definition.category_key,
                title=definition.title,
                subtitle=definition.subtitle,
                category_filters=await self._record_category_filters(definition.category_key),
            ),
            tuple(
                RecordHolderProjectionSource(
                    build_id=holder.build_id,
                    title=title,
                    creators=tuple(creators or ()),
                    creator_ids=tuple(creator_ids or ()),
                    metric=holder.metric_snapshot,
                )
                for holder, title, creators, creator_ids in holders
            ),
        )

    async def _record_category_filters(self, key: str) -> tuple[ProjectionFilter, ...]:
        identity = CategoryIdentity.from_key(key)
        if identity is None:
            return ()

        filters: list[ProjectionFilter] = []
        pattern_ids: frozenset[int] = frozenset()
        if identity.kind is BuildKind.DOOR and (door := door_category_lookup(identity)) is not None:
            width, height, depth = door.opening
            filters.extend(
                (
                    ProjectionFilter("door_width", Decimal(width)),
                    ProjectionFilter("door_height", Decimal(height)),
                    ProjectionFilter("door_depth", Decimal(depth)),
                    ProjectionFilter("orientation", door.orientation),
                )
            )
            pattern_ids = door.type_ids
            if not pattern_ids:
                filters.append(ProjectionFilter("type", "Regular"))
        elif identity.kind is BuildKind.EXTENDER:
            parts = identity.base_key.split("|", maxsplit=3)
            if len(parts) == 4 and parts[0] == "extender":
                _, orientation, length, types = parts
                if length.isdecimal():
                    filters.extend(
                        (
                            ProjectionFilter("extender_length", Decimal(length)),
                            ProjectionFilter("orientation", orientation),
                        )
                    )
                if types.startswith("t[") and types.endswith("]"):
                    type_key = types[2:-1]
                    if all(part.isdecimal() for part in type_key.split(",")):
                        pattern_ids = frozenset(int(part) for part in type_key.split(","))
                    elif type_key:
                        # Extender categories without pattern tags use the stored type name.
                        filters.append(ProjectionFilter("type", type_key))

        restriction_ids = frozenset(identity.restriction_ids)
        tag_ids = pattern_ids | restriction_ids
        if tag_ids:
            filters.extend(ProjectionFilter("category_tag_id", Decimal(tag_id)) for tag_id in sorted(tag_ids))
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
                filters.extend(ProjectionFilter(field_name, name) for name in sorted(names))
        return tuple(filters)

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

    async def process_batch(self, *, limit: int = 50) -> tuple[int, int]:
        """Process queue items and return successful and failed counts."""
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
                    if resource_kind == "build" and source_key.isdecimal():
                        await self._session.execute(
                            select(
                                func.upsert_search_projection_request(
                                    "record", func.concat("result:", RecordResult.id), "upsert"
                                )
                            )
                            .select_from(RecordResult)
                            .join(RecordHolder, RecordHolder.result_id == RecordResult.id)
                            .join(ComputationRun, ComputationRun.id == RecordResult.run_id)
                            .where(RecordHolder.build_id == int(source_key), ComputationRun.is_active.is_(True))
                            .order_by(RecordResult.id)
                        )
                    if resource_kind == "metadata" and (key := parse_projection_key(resource_kind, source_key)):
                        subtype, tag_id = key
                        if subtype == "tag":
                            await self._session.execute(
                                select(
                                    func.upsert_search_projection_request("record", SearchDocument.source_key, "upsert")
                                )
                                .select_from(SearchDocumentFilter)
                                .join(SearchDocument, SearchDocument.id == SearchDocumentFilter.document_id)
                                .where(
                                    SearchDocument.resource_kind == "record",
                                    SearchDocumentFilter.field_name == "category_tag_id",
                                    SearchDocumentFilter.numeric_value == Decimal(tag_id),
                                )
                                .order_by(SearchDocument.source_key)
                            )
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
        return succeeded, failed


class PostgresSearchProjection:
    """Run projection work in the transaction supplied by the unit of work."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def process_batch(self, *, limit: int = 50) -> tuple[int, int]:
        return await SearchProjectionWorker(self._session()).process_batch(limit=limit)


def parse_projection_key(resource_kind: str, source_key: str) -> tuple[str, int] | None:
    """Return the subtype and numeric ID encoded by a queue key.

    Builds use a bare ID, records use `result:ID`, and metadata uses `subtype:ID`.
    Return None for an unsupported resource kind or malformed key. Metadata
    subtypes are checked later by the loader.
    """
    if resource_kind == "build" and source_key.isdecimal():
        return "build", int(source_key)
    if resource_kind == "record":
        subtype, separator, raw_id = source_key.partition(":")
        if separator and subtype == "result" and raw_id.isdecimal():
            return subtype, int(raw_id)
        return None
    if resource_kind == "metadata":
        subtype, separator, raw_id = source_key.partition(":")
        if separator and raw_id.isdecimal():
            return subtype, int(raw_id)
    return None


def build_record_projection(
    source: RecordProjectionSource,
    holders: Sequence[RecordHolderProjectionSource] = (),
) -> SearchProjection:
    """Index each record with the builds and creators that currently hold it."""
    first_holder = holders[0] if holders else None
    holder_ids = tuple(holder.build_id for holder in holders)
    tags = (source.criterion, source.build_kind, source.version_scope)
    return SearchProjection(
        resource_kind="record",
        source_key=f"result:{source.result_id}",
        title=source.title,
        subtitle=source.subtitle,
        description="\n".join(" ".join((holder.title, *holder.creators)) for holder in holders) or None,
        status=source.status,
        tags=tags,
        document_data={
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
            "history_complete": source.history_complete,
            "gap_reasons": source.gap_reasons,
        },
        filters=(
            ProjectionFilter("base_title", source.criterion),
            ProjectionFilter("record_state", "current"),
            ProjectionFilter("kind", source.build_kind),
            ProjectionFilter("version_scope", source.version_scope),
            *source.category_filters,
            *(
                ProjectionFilter("creator", name)
                for name in dict.fromkeys(n for holder in holders for n in holder.creators)
            ),
            *(
                ProjectionFilter(CREATOR_ID_STORAGE, public_id)
                for public_id in dict.fromkeys(i for holder in holders for i in holder.creator_ids)
            ),
            *(value for holder in holders for value in holder.filters(source.build_kind)),
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
        tags=source.aliases,
        extra_filters=(
            ProjectionFilter("kind", "tag"),
            *(ProjectionFilter(source.semantic_kind, alias) for alias in source.aliases),
            *(ProjectionFilter("tag", name) for name in (source.display_name, *source.aliases)),
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
    tags: Sequence[str] = (),
    extra_filters: Sequence[ProjectionFilter] = (),
    data: dict[str, object],
) -> SearchProjection:
    return SearchProjection(
        resource_kind="metadata",
        source_key=source_key,
        title=title,
        tags=tuple(tags),
        document_data={"metadata_kind": subtype, **data},
        filters=(ProjectionFilter("kind", subtype), ProjectionFilter(subtype, title), *extra_filters),
    )


def build_filter_models(document_id: int, filters: Sequence[ProjectionFilter]) -> list[SearchDocumentFilter]:
    """Number indexed values separately for each field."""
    models: list[SearchDocumentFilter] = []
    ordinals: dict[str, int] = {}
    for filter_value in filters:
        ordinal = ordinals.get(filter_value.field_name, 0)
        ordinals[filter_value.field_name] = ordinal + 1
        text_value: str | None = None
        numeric_value: Decimal | None = None
        timestamp_value: Instant | None = None
        boolean_value: bool | None = None
        if isinstance(filter_value.value, bool):
            boolean_value = filter_value.value
        elif isinstance(filter_value.value, Decimal):
            numeric_value = filter_value.value
        elif isinstance(filter_value.value, Instant):
            timestamp_value = filter_value.value
        else:
            text_value = filter_value.value
        models.append(
            SearchDocumentFilter(
                document_id=document_id,
                field_name=filter_value.field_name,
                ordinal=ordinal,
                holder_build_id=filter_value.holder_build_id,
                text_value=text_value,
                numeric_value=numeric_value,
                timestamp_value=timestamp_value,
                boolean_value=boolean_value,
            )
        )
    return models


def normalize_search_text(value: str) -> str:
    """Case-fold text and collapse whitespace to single spaces."""
    return " ".join(value.casefold().split())


def build_tag_name_filters(assignments: Sequence[TagAssignment]) -> tuple[ProjectionFilter, ...]:
    """Index assigned names and aliases without changing the displayed tags."""
    filters: list[ProjectionFilter] = []
    for assignment in assignments:
        definition = assignment.definition
        names = tuple(dict.fromkeys((definition.display_name, *(alias.alias for alias in definition.aliases))))
        tag_names = tuple(dict.fromkeys((assignment.rendered, *names)))
        filters.extend(ProjectionFilter("tag", name) for name in tag_names)
        if definition.authority == "official":
            fields = {"restriction": ("restriction",), "pattern": ("type", "pattern")}.get(definition.semantic_kind, ())
            filters.extend(ProjectionFilter(field, name) for field in fields for name in names)
    return tuple(filters)


def projection_source_hash(projection: SearchProjection) -> str:
    """Hash document content to detect changes that invalidate an embedding.

    Dictionary keys are sorted, but tag and indexed field value order remains significant.
    Resource identity is excluded because the hash tracks content within a document.
    """
    serialized = json.dumps(
        {
            "title": projection.title,
            "subtitle": projection.subtitle,
            "description": projection.description,
            "status": projection.status,
            "tags": projection.tags,
            "search_aliases": projection.search_aliases,
            "document_data": projection.document_data,
            "filters": tuple(
                (value.field_name, value.value)
                if value.holder_build_id is None
                else (value.field_name, value.value, value.holder_build_id)
                for value in projection.filters
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


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
