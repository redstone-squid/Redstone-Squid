"""SQLAlchemy persistence adapter for generated schematic previews."""

import hashlib
from typing import cast

from sqlalchemy import Select, Table, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.artifacts import ArtifactStore
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.models import BuildLink
from squid.core.errors import DataIntegrityError
from squid.persistence.types import now
from squid.schematics.application.previews import PreviewObjectReservation, StoredRender
from squid.schematics.infrastructure.models import (
    BuildSchematic,
    SchematicPreviewObject,
    SchematicRender,
    SchematicRenderQueueItem,
)

_BUILD_TABLE = cast(Table, SQLBuild.__table__)


class PostgresSchematicPreviewPublisher:
    """Persist preview recipes and atomically publish their generated build links."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], artifacts: ArtifactStore) -> None:
        self._session_factory = session_factory
        self._artifacts = artifacts

    async def featured_attachment_changed(self, session: AsyncSession, build_id: int) -> None:
        """Clear the old generated link and enqueue replacement within the caller's transaction.

        The attachment store must already hold the build-row lock. Keeping this operation on
        the caller's session makes attachment promotion, generated-link removal, and durable
        preview intent one atomic change while this adapter remains the sole owner of preview
        tables and generated build links.
        """
        await self._clear_generated_preview_link(session, build_id)
        render_job = insert(SchematicRenderQueueItem).values(build_id=build_id)
        await session.execute(
            render_job.on_conflict_do_update(
                index_elements=[SchematicRenderQueueItem.build_id],
                set_={
                    "enqueued_at": func.now(),
                    "available_at": func.now(),
                    "claimed_at": None,
                    "claim_token": None,
                    "dead_at": None,
                    "attempts": 0,
                    "last_error": None,
                },
            )
        )

    async def get_render(self, schematic_id: int, recipe_hash: str) -> StoredRender | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SchematicRender).where(
                    and_(
                        SchematicRender.build_schematic_id == schematic_id,
                        SchematicRender.recipe_hash == recipe_hash,
                    )
                )
            )
        if row is None or row.object_key is None:
            return None
        preview_object = await self._preview_object(row.object_key)
        if preview_object is None or preview_object.ready_at is None:
            return None
        if not await self._stored_object_is_ready(preview_object):
            await self._mark_object_not_ready(preview_object.object_key)
            return None
        return _stored_render(row)

    async def reserve_preview_object(
        self,
        object_key: str,
        *,
        byte_size: int,
        sha256: str,
    ) -> PreviewObjectReservation:
        """Reserve durable ownership before upload and identify reusable ready bytes."""
        seen_at = now()
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SchematicPreviewObject).where(SchematicPreviewObject.object_key == object_key).with_for_update()
            )
            if row is None:
                row = SchematicPreviewObject(
                    object_key=object_key,
                    byte_size=byte_size,
                    sha256=sha256,
                    last_seen_at=seen_at,
                )
                session.add(row)
                reusable = False
            else:
                referenced = bool(
                    await session.scalar(
                        select(func.count())
                        .select_from(SchematicRender)
                        .where(SchematicRender.object_key == object_key)
                    )
                )
                metadata_changed = row.byte_size != byte_size or row.sha256 not in (None, sha256)
                if metadata_changed and referenced:
                    msg = "A referenced schematic preview object was reserved with different immutable metadata."
                    raise DataIntegrityError(msg, context={"object_key": object_key})
                if metadata_changed:
                    row.byte_size = byte_size
                    row.sha256 = sha256
                    row.ready_at = None
                elif row.sha256 is None:
                    row.sha256 = sha256
                row.last_seen_at = seen_at
                reusable = row.ready_at is not None
            await session.commit()

        if reusable and await self._stored_object_is_ready(row):
            return PreviewObjectReservation(object_key, byte_size, sha256, upload_required=False)
        if reusable:
            await self._mark_object_not_ready(object_key)
        return PreviewObjectReservation(object_key, byte_size, sha256, upload_required=True)

    async def mark_preview_object_ready(self, reservation: PreviewObjectReservation) -> None:
        """Mark a reserved object reusable after its upload metadata was verified."""
        ready_at = now()
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SchematicPreviewObject)
                .where(SchematicPreviewObject.object_key == reservation.object_key)
                .with_for_update()
            )
            if row is None or row.byte_size != reservation.byte_size or row.sha256 not in (None, reservation.sha256):
                msg = "Schematic preview object reservation no longer matches its uploaded bytes."
                raise DataIntegrityError(msg, context={"object_key": reservation.object_key})
            row.sha256 = reservation.sha256
            row.ready_at = ready_at
            row.last_seen_at = ready_at
            await session.commit()

    async def publish_fresh_preview(
        self,
        schematic_id: int,
        recipe_hash: str,
        url: str,
        object_key: str,
        *,
        width: int,
        height: int,
        byte_size: int,
    ) -> StoredRender | None:
        """Publish one generated preview only if its source remains the featured attachment.

        Attachment replacement takes the same build-row lock before changing the featured
        attachment. Whichever transaction wins therefore leaves a coherent result: either
        this URL is published while its source is current, or replacement clears/rejects it.
        """
        statement = (
            insert(SchematicRender)
            .values(
                build_schematic_id=schematic_id,
                recipe_hash=recipe_hash,
                url=url,
                object_key=object_key,
                width=width,
                height=height,
                byte_size=byte_size,
            )
            .on_conflict_do_update(
                index_elements=[SchematicRender.build_schematic_id, SchematicRender.recipe_hash],
                set_={
                    "url": url,
                    "object_key": object_key,
                    "width": width,
                    "height": height,
                    "byte_size": byte_size,
                },
            )
            .returning(SchematicRender)
        )
        async with self._session_factory() as session:
            build_id = await session.scalar(select(BuildSchematic.build_id).where(BuildSchematic.id == schematic_id))
            if build_id is None:
                return None
            await session.scalar(select(_BUILD_TABLE.c.id).where(_BUILD_TABLE.c.id == build_id).with_for_update())
            preview_object = await session.scalar(
                select(SchematicPreviewObject).where(SchematicPreviewObject.object_key == object_key).with_for_update()
            )
            if preview_object is None or preview_object.ready_at is None or preview_object.byte_size != byte_size:
                msg = "A generated preview cannot be published before its object is ready."
                raise DataIntegrityError(msg, context={"object_key": object_key})
            current_id = await session.scalar(
                select(BuildSchematic.id).where(
                    BuildSchematic.build_id == build_id,
                    BuildSchematic.is_primary,
                )
            )
            if current_id != schematic_id:
                return None
            previous_url = await session.scalar(
                select(SchematicRender.url).where(
                    SchematicRender.build_schematic_id == schematic_id,
                    SchematicRender.recipe_hash == recipe_hash,
                )
            )
            row = await session.scalar(statement)
            await self.replace_generated_preview_link(session, build_id, url, previous_url=previous_url)
            await session.commit()
        assert row is not None
        return _stored_render(row)

    async def publish_cached_preview(self, schematic_id: int, recipe_hash: str, url: str) -> bool:
        """Publish an existing recipe only if its source remains featured."""
        async with self._session_factory() as session:
            build_id = await session.scalar(select(BuildSchematic.build_id).where(BuildSchematic.id == schematic_id))
            if build_id is None:
                return False
            await session.scalar(select(_BUILD_TABLE.c.id).where(_BUILD_TABLE.c.id == build_id).with_for_update())
            current_id = await session.scalar(
                select(BuildSchematic.id).where(
                    BuildSchematic.build_id == build_id,
                    BuildSchematic.is_primary,
                )
            )
            if current_id != schematic_id:
                return False
            render = await session.scalar(
                select(SchematicRender).where(
                    SchematicRender.build_schematic_id == schematic_id,
                    SchematicRender.recipe_hash == recipe_hash,
                    SchematicRender.url == url,
                )
            )
            if render is None or render.object_key is None:
                return False
            preview_object = await session.scalar(
                select(SchematicPreviewObject)
                .where(SchematicPreviewObject.object_key == render.object_key)
                .with_for_update()
            )
            if preview_object is None or preview_object.ready_at is None:
                return False
            if not await self._stored_object_is_ready(preview_object):
                preview_object.ready_at = None
                await session.commit()
                msg = "A cached schematic preview disappeared after it was selected for publication."
                raise DataIntegrityError(msg, context={"object_key": preview_object.object_key})
            await self.replace_generated_preview_link(session, build_id, url)
            await session.commit()
        return True

    @staticmethod
    async def replace_generated_preview_link(
        session: AsyncSession,
        build_id: int,
        url: str,
        *,
        previous_url: str | None = None,
    ) -> None:
        """Atomically replace only the build link owned by registered preview artifacts."""
        registered_urls = PostgresSchematicPreviewPublisher._registered_preview_urls(build_id)
        owned_url = BuildLink.url.in_(registered_urls)
        if previous_url is not None:
            owned_url = or_(owned_url, BuildLink.url == previous_url)
        existing = tuple(
            await session.scalars(
                select(BuildLink.url).where(
                    BuildLink.build_id == build_id,
                    BuildLink.media_type == "render",
                    owned_url,
                )
            )
        )
        if existing == (url,):
            return
        await session.execute(
            delete(BuildLink).where(
                BuildLink.build_id == build_id,
                BuildLink.media_type == "render",
                owned_url,
            )
        )
        await session.execute(insert(BuildLink).values(build_id=build_id, url=url, media_type="render"))
        await session.execute(
            update(_BUILD_TABLE).where(_BUILD_TABLE.c.id == build_id).values(revision=_BUILD_TABLE.c.revision + 1)
        )

    @staticmethod
    async def _clear_generated_preview_link(session: AsyncSession, build_id: int) -> None:
        registered_urls = PostgresSchematicPreviewPublisher._registered_preview_urls(build_id)
        existing = await session.scalar(
            select(BuildLink.url)
            .where(
                BuildLink.build_id == build_id,
                BuildLink.media_type == "render",
                BuildLink.url.in_(registered_urls),
            )
            .limit(1)
        )
        if existing is None:
            return
        await session.execute(
            delete(BuildLink).where(
                BuildLink.build_id == build_id,
                BuildLink.media_type == "render",
                BuildLink.url.in_(registered_urls),
            )
        )
        await session.execute(
            update(_BUILD_TABLE).where(_BUILD_TABLE.c.id == build_id).values(revision=_BUILD_TABLE.c.revision + 1)
        )

    @staticmethod
    def _registered_preview_urls(build_id: int) -> Select[tuple[str]]:
        """Return generated preview URLs whose lifecycle this publisher owns."""
        return (
            select(SchematicRender.url)
            .join(BuildSchematic, SchematicRender.build_schematic_id == BuildSchematic.id)
            .where(BuildSchematic.build_id == build_id)
        )

    async def get_render_content(self, recipe_hash: str, *, max_bytes: int) -> bytes | None:
        """Load a registered preview artifact within the API response budget."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(
                        SchematicRender.object_key,
                        SchematicPreviewObject.byte_size,
                        SchematicPreviewObject.sha256,
                        SchematicPreviewObject.ready_at,
                    )
                    .join(SchematicPreviewObject, SchematicRender.object_key == SchematicPreviewObject.object_key)
                    .where(SchematicRender.recipe_hash == recipe_hash)
                )
            ).first()
        if row is None or row.object_key is None or row.ready_at is None or row.byte_size > max_bytes:
            return None
        content = await self._artifacts.get(row.object_key, max_bytes=max_bytes)
        if (
            content is None
            or len(content) != row.byte_size
            or (row.sha256 is not None and hashlib.sha256(content).hexdigest() != row.sha256)
        ):
            await self._mark_object_not_ready(row.object_key)
            return None
        return content

    async def cleanup_unreferenced_preview_objects(self, *, older_than: Instant, limit: int) -> int:
        """Delete old objects only while their lifecycle rows remain unreferenced and locked."""
        removed = 0
        for _ in range(limit):
            async with self._session_factory() as session:
                row = await session.scalar(
                    select(SchematicPreviewObject)
                    .where(
                        SchematicPreviewObject.last_seen_at < older_than,
                        ~select(SchematicRender.id)
                        .where(SchematicRender.object_key == SchematicPreviewObject.object_key)
                        .exists(),
                    )
                    .order_by(SchematicPreviewObject.last_seen_at, SchematicPreviewObject.object_key)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    break
                await self._artifacts.delete(row.object_key)
                await session.delete(row)
                await session.commit()
                removed += 1
        return removed

    async def _preview_object(self, object_key: str) -> SchematicPreviewObject | None:
        async with self._session_factory() as session:
            return await session.get(SchematicPreviewObject, object_key)

    async def _stored_object_is_ready(self, row: SchematicPreviewObject) -> bool:
        metadata = await self._artifacts.stat(row.object_key)
        return metadata is not None and metadata.byte_size == row.byte_size and metadata.sha256 in (None, row.sha256)

    async def _mark_object_not_ready(self, object_key: str) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(SchematicPreviewObject)
                .where(SchematicPreviewObject.object_key == object_key)
                .values(ready_at=None, last_seen_at=func.now())
            )


def _stored_render(row: SchematicRender) -> StoredRender:
    return StoredRender(
        schematic_id=row.build_schematic_id,
        recipe_hash=row.recipe_hash,
        url=row.url,
        width=row.width,
        height=row.height,
        byte_size=row.byte_size,
    )
