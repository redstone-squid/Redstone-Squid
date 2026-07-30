"""Durable refresh infrastructure for indexed search documents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import case, delete, func, literal_column, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from whenever import Instant

from squid.builds.errors import InvalidBuildError
from squid.builds.infrastructure.mapping import BuildMapper
from squid.builds.infrastructure.models import (
    Build,
    BuildCreator,
    BuildRestriction,
    BuildType,
    BuildVersion,
    Restriction,
    RestrictionAlias,
    SmallestDoor,
    Type,
)
from squid.core.errors import DataIntegrityError
from squid.records.infrastructure.models import (
    RecordComputationRun,
    RecordDefinition,
    RecordResult,
    RecordResultHolder,
)
from squid.search.infrastructure.models import (
    SearchDocument,
    SearchDocumentFacet,
    SearchEmbeddingQueueItem,
    SearchProjectionQueueItem,
)
from squid.users.infrastructure.models import User
from squid.versions.infrastructure.models import Version


@dataclass(frozen=True, slots=True)
class ProjectionFacet:
    """One typed filter value emitted by a search projection."""

    field_name: str
    value: str | Decimal | Instant | bool


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
    document_data: dict[str, object] = field(default_factory=dict)
    facets: tuple[ProjectionFacet, ...] = ()


class SearchProjectionStore:
    """Claim projection work and atomically replace indexed documents."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, *, limit: int = 50) -> tuple[SearchProjectionQueueItem, ...]:
        """Lock and mark the oldest available queue items for this worker."""
        items = tuple(
            (
                await self._session.scalars(
                    select(SearchProjectionQueueItem)
                    .where(
                        or_(
                            SearchProjectionQueueItem.locked_at.is_(None),
                            SearchProjectionQueueItem.locked_at < func.now() - text("interval '5 minutes'"),
                        )
                    )
                    .order_by(SearchProjectionQueueItem.enqueued_at, SearchProjectionQueueItem.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        now = Instant.now()
        for item in items:
            item.locked_at = now
        await self._session.flush()
        return items

    async def complete(self, item: SearchProjectionQueueItem) -> None:
        """Remove a successfully processed queue item."""
        await self._session.delete(item)

    async def retry(self, item: SearchProjectionQueueItem, error: Exception) -> None:
        """Release failed work for retry while retaining diagnostic context."""
        item.attempts += 1
        item.locked_at = None
        item.last_error = str(error)[:4000]
        await self._session.flush()

    async def delete_document(self, resource_kind: str, source_key: str) -> None:
        """Delete a projected resource and its cascading facets/embedding work."""
        await self._session.execute(
            delete(SearchDocument).where(
                SearchDocument.resource_kind == resource_kind,
                SearchDocument.source_key == source_key,
            )
        )

    async def replace(self, projection: SearchProjection) -> int:
        """Upsert a document and replace all typed facets in the same transaction."""
        normalized_title = normalize_search_text(projection.title)
        tags = sorted({normalize_search_text(tag) for tag in projection.tags if tag.strip()})
        fuzzy_text = " ".join(
            part for part in (normalized_title, normalize_search_text(projection.subtitle or ""), *tags) if part
        )
        source_hash = projection_source_hash(projection)
        title_vector = func.to_tsvector(literal_column("'simple'"), func.unaccent(projection.title))
        description_vector = func.to_tsvector(
            literal_column("'simple'"),
            func.unaccent(projection.description or ""),
        )
        tag_text = " ".join(tags)
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
        await self._session.execute(delete(SearchDocumentFacet).where(SearchDocumentFacet.document_id == document_id))
        self._session.add_all(build_facet_models(document_id, projection.facets))
        if projection.resource_kind in {"build", "record"}:
            embedding_statement = insert(SearchEmbeddingQueueItem).values(
                document_id=document_id,
                source_hash=source_hash,
                enqueued_at=func.now(),
                attempts=0,
                locked_at=None,
                last_error=None,
            )
            await self._session.execute(
                embedding_statement.on_conflict_do_update(
                    index_elements=[SearchEmbeddingQueueItem.document_id],
                    set_={
                        "source_hash": embedding_statement.excluded.source_hash,
                        "enqueued_at": func.now(),
                        "attempts": 0,
                        "locked_at": None,
                        "last_error": None,
                    },
                    where=SearchEmbeddingQueueItem.source_hash != embedding_statement.excluded.source_hash,
                )
            )
        await self._session.flush()
        return document_id


class SearchProjectionLoader:
    """Build search projections from current application-owned data."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._build_mapper = BuildMapper()

    async def load(self, resource_kind: str, source_key: str) -> SearchProjection | None:
        """Load the requested resource, returning None when its source was deleted."""
        if resource_kind == "build":
            return await self._build(int(source_key))
        if resource_kind == "record" and source_key.startswith("legacy-smallest:"):
            return await self._legacy_smallest(int(source_key.partition(":")[2]))
        if resource_kind == "record" and source_key.startswith("result:"):
            return await self._computed_record(int(source_key.partition(":")[2]))
        if resource_kind == "metadata":
            subtype, separator, raw_id = source_key.partition(":")
            if not separator:
                return None
            return await self._metadata(subtype, int(raw_id))
        return None

    async def _build(self, build_id: int) -> SearchProjection | None:
        build = await self._session.scalar(
            select(Build)
            .where(Build.id == build_id)
            .options(
                selectinload(Build.build_restrictions).selectinload(BuildRestriction.restriction),
                selectinload(Build.build_types).selectinload(BuildType.type),
                selectinload(Build.links),
            )
        )
        if build is None:
            return None
        restrictions = tuple(
            (
                await self._session.scalars(
                    select(Restriction)
                    .join(BuildRestriction, BuildRestriction.restriction_id == Restriction.id)
                    .where(BuildRestriction.build_id == build_id)
                    .order_by(Restriction.name)
                )
            ).all()
        )
        types = tuple(
            (
                await self._session.scalars(
                    select(Type)
                    .join(BuildType, BuildType.type_id == Type.id)
                    .where(BuildType.build_id == build_id)
                    .order_by(Type.name)
                )
            ).all()
        )
        creators = tuple(
            (
                await self._session.scalars(
                    select(User)
                    .join(BuildCreator, BuildCreator.user_id == User.id)
                    .where(BuildCreator.build_id == build_id)
                    .order_by(User.ign)
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
        try:
            title = (await self._build_mapper.to_domain(self._session, build)).title
        except (DataIntegrityError, InvalidBuildError, NotImplementedError, TypeError, ValueError):
            title = f"{build.category or 'Build'} #{build.id}"
        description = build.description
        if description is None:
            submitted_description = build.extra_info.get("user")
            description = submitted_description if isinstance(submitted_description, str) else None
        restriction_names = tuple(
            name for restriction in restrictions if (name := _mapped_text(restriction, "name")) is not None
        )
        type_names = tuple(name for build_type in types if (name := _mapped_text(build_type, "name")) is not None)
        creator_names = tuple(name for creator in creators if (name := _mapped_text(creator, "ign")) is not None)
        version_names = tuple(_version_name(version) for version in versions)
        dimensions = {
            name: value
            for name, value in (("width", build.width), ("height", build.height), ("depth", build.depth))
            if value is not None
        }
        facets = [
            *(ProjectionFacet("restriction", name) for name in restriction_names),
            *(ProjectionFacet("type", name) for name in type_names),
            *(ProjectionFacet("pattern", name) for name in type_names),
            *(ProjectionFacet("creator", name) for name in creator_names),
            *(ProjectionFacet("version", name) for name in version_names),
            ProjectionFacet("status", build.submission_status.name.lower()),
            ProjectionFacet("kind", build.category or "unknown"),
            *(ProjectionFacet(name, Decimal(value)) for name, value in dimensions.items()),
        ]
        if build.completion_at is not None:
            facets.append(ProjectionFacet("completion_at", build.completion_at))
        return SearchProjection(
            resource_kind="build",
            source_key=str(build.id),
            title=title,
            description=description,
            status=build.submission_status.name.lower(),
            tags=(*restriction_names, *type_names, *creator_names, *version_names),
            document_data={
                "build_id": build.id,
                "category": build.category,
                "dimensions": dimensions,
                "creators": creator_names,
                "restrictions": restriction_names,
                "types": type_names,
                "versions": version_names,
            },
            facets=tuple(facets),
        )

    async def _legacy_smallest(self, record_id: int) -> SearchProjection | None:
        record = await self._session.scalar(select(SmallestDoor).where(SmallestDoor.record_id == record_id))
        if record is None:
            return None
        title = record.title or f"Smallest {record.door_width}x{record.door_height} {record.orientation}"
        tags = tuple(dict.fromkeys((*record.types, *record.restriction_subset)))
        return SearchProjection(
            resource_kind="record",
            source_key=f"legacy-smallest:{record.record_id}",
            title=title,
            subtitle="Legacy computed smallest-door record",
            status="current",
            tags=tags,
            document_data={
                "record_id": record.record_id,
                "build_id": record.id,
                "record_class": "smallest",
                "legacy": True,
                "volume": record.volume,
                "restrictions": record.restriction_subset,
                "types": record.types,
            },
            facets=(
                ProjectionFacet("record_class", "smallest"),
                ProjectionFacet("record_state", "current"),
                ProjectionFacet("kind", "Door"),
                ProjectionFacet("volume", Decimal(record.volume)),
                ProjectionFacet("width", Decimal(record.door_width)),
                ProjectionFacet("height", Decimal(record.door_height)),
                ProjectionFacet("depth", Decimal(record.door_depth)),
                *(ProjectionFacet("restriction", name) for name in record.restriction_subset),
                *(ProjectionFacet("type", name) for name in record.types),
                *(ProjectionFacet("pattern", name) for name in record.types),
            ),
        )

    async def _computed_record(self, result_id: int) -> SearchProjection | None:
        row = (
            await self._session.execute(
                select(RecordResult, RecordDefinition)
                .join(RecordComputationRun, RecordComputationRun.id == RecordResult.run_id)
                .join(RecordDefinition, RecordDefinition.id == RecordResult.definition_id)
                .where(RecordResult.id == result_id, RecordComputationRun.is_active.is_(True))
            )
        ).one_or_none()
        if row is None:
            return None
        result, definition = row
        holders = tuple(
            (
                await self._session.scalars(
                    select(RecordResultHolder)
                    .where(RecordResultHolder.result_id == result_id)
                    .order_by(RecordResultHolder.rank, RecordResultHolder.build_id)
                )
            ).all()
        )
        first_holder = holders[0] if holders else None
        title = (
            first_holder.title
            if first_holder is not None
            else f"{definition.record_class.title()} {definition.category_key}"
        )
        subtitle = first_holder.subtitle if first_holder is not None else None
        holder_ids = tuple(holder.build_id for holder in holders)
        metric = first_holder.metric_snapshot if first_holder is not None else {}
        tags = (definition.record_class, definition.build_kind, definition.category_key, definition.version_scope)
        facets = (
            ProjectionFacet("record_class", definition.record_class),
            ProjectionFacet("record_state", "current"),
            ProjectionFacet("kind", definition.build_kind),
            ProjectionFacet("version_scope", definition.version_scope),
            *(ProjectionFacet("holder", str(build_id)) for build_id in holder_ids),
        )
        return SearchProjection(
            resource_kind="record",
            source_key=f"result:{result_id}",
            title=title,
            subtitle=subtitle,
            status=result.status,
            tags=tags,
            document_data={
                "result_id": result.id,
                "definition_id": definition.id,
                "record_class": definition.record_class,
                "build_kind": definition.build_kind,
                "version_scope": definition.version_scope,
                "category_key": definition.category_key,
                "holder_build_ids": holder_ids,
                "metric": metric,
                "history_complete": result.history_complete,
                "gap_reasons": result.gap_reasons,
            },
            facets=facets,
        )

    async def _metadata(self, subtype: str, source_id: int) -> SearchProjection | None:
        if subtype == "restriction":
            restriction = await self._session.get(Restriction, source_id)
            if restriction is None:
                return None
            aliases = tuple(
                (
                    await self._session.scalars(
                        select(RestrictionAlias.alias)
                        .where(RestrictionAlias.restriction_id == source_id)
                        .order_by(RestrictionAlias.alias)
                    )
                ).all()
            )
            restriction_name = _mapped_text(restriction, "name")
            if restriction_name is None:
                return None
            return _metadata_projection(
                source_key=f"restriction:{source_id}",
                title=restriction_name,
                subtype="restriction",
                tags=aliases,
                data={
                    "restriction_id": source_id,
                    "aliases": aliases,
                    "build_category": restriction.build_category,
                    "restriction_type": restriction.type,
                },
            )
        if subtype == "type":
            build_type = await self._session.get(Type, source_id)
            if build_type is None:
                return None
            type_name = _mapped_text(build_type, "name")
            if type_name is None:
                return None
            return _metadata_projection(
                source_key=f"type:{source_id}",
                title=type_name,
                subtype="type",
                data={"type_id": source_id, "build_category": build_type.build_category},
            )
        if subtype == "creator":
            creator = await self._session.get(User, source_id)
            if creator is None:
                return None
            creator_name = _mapped_text(creator, "ign")
            if creator_name is None:
                return None
            return _metadata_projection(
                source_key=f"creator:{source_id}",
                title=creator_name,
                subtype="creator",
                data={"user_id": source_id, "discord_id": creator.discord_id},
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
        self._store = SearchProjectionStore(session)
        self._loader = SearchProjectionLoader(session)

    async def process_batch(self, *, limit: int = 50) -> tuple[int, int]:
        """Process queue items and return successful and failed counts."""
        succeeded = 0
        failed = 0
        for item in await self._store.claim(limit=limit):
            item_id = item.id
            try:
                async with self._session.begin_nested():
                    if item.action == "delete":
                        await self._store.delete_document(item.resource_kind, item.source_key)
                    else:
                        projection = await self._loader.load(item.resource_kind, item.source_key)
                        if projection is None:
                            await self._store.delete_document(item.resource_kind, item.source_key)
                        else:
                            await self._store.replace(projection)
                    await self._store.complete(item)
                succeeded += 1
            except Exception as error:
                retry_item = await self._session.get(SearchProjectionQueueItem, item_id)
                if retry_item is not None:
                    await self._store.retry(retry_item, error)
                failed += 1
        return succeeded, failed


async def run_projection_batch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int = 50,
) -> tuple[int, int]:
    """Run and commit one bounded worker batch using an application session factory."""
    async with session_factory() as session, session.begin():
        return await SearchProjectionWorker(session).process_batch(limit=limit)


def _metadata_projection(
    *,
    source_key: str,
    title: str,
    subtype: str,
    tags: Sequence[str] = (),
    data: dict[str, object],
) -> SearchProjection:
    return SearchProjection(
        resource_kind="metadata",
        source_key=source_key,
        title=title,
        tags=tuple(tags),
        document_data={"metadata_kind": subtype, **data},
        facets=(ProjectionFacet("kind", subtype), ProjectionFacet(subtype, title)),
    )


def build_facet_models(document_id: int, facets: Sequence[ProjectionFacet]) -> list[SearchDocumentFacet]:
    models: list[SearchDocumentFacet] = []
    ordinals: dict[str, int] = {}
    for facet in facets:
        ordinal = ordinals.get(facet.field_name, 0)
        ordinals[facet.field_name] = ordinal + 1
        text_value: str | None = None
        numeric_value: Decimal | None = None
        timestamp_value: Instant | None = None
        boolean_value: bool | None = None
        if isinstance(facet.value, bool):
            boolean_value = facet.value
        elif isinstance(facet.value, Decimal):
            numeric_value = facet.value
        elif isinstance(facet.value, Instant):
            timestamp_value = facet.value
        else:
            text_value = facet.value
        models.append(
            SearchDocumentFacet(
                document_id=document_id,
                field_name=facet.field_name,
                ordinal=ordinal,
                text_value=text_value,
                numeric_value=numeric_value,
                timestamp_value=timestamp_value,
                boolean_value=boolean_value,
            )
        )
    return models


def normalize_search_text(value: str) -> str:
    return " ".join(value.casefold().split())


def projection_source_hash(projection: SearchProjection) -> str:
    serialized = json.dumps(
        {
            "title": projection.title,
            "subtitle": projection.subtitle,
            "description": projection.description,
            "status": projection.status,
            "tags": projection.tags,
            "document_data": projection.document_data,
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
