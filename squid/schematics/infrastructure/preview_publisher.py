"""SQLAlchemy persistence adapter for generated schematic previews."""

from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.builds.history import BuildChange
from squid.builds.infrastructure.history import record_build_changes
from squid.builds.models import Build, BuildLink
from squid.core.errors import DataIntegrityError
from squid.persistence.types import now
from squid.schematics.application.preview_storage import PreviewCleanupClaim, PreviewCleanupInProgressError
from squid.schematics.application.previews import PreviewObjectReservation, StoredRender
from squid.schematics.models import (
    BuildSchematic,
    SchematicPreviewObject,
    SchematicRender,
    SchematicRenderQueueItem,
)


class PostgresSchematicPreviewRepository:
    """Persist preview recipes and generated build links within the caller's transaction.

    Publication locks the build before checking its featured attachment, coordinating with
    attachment replacement. Object reservations and cleanup claims prevent publication from
    reusing bytes while deletion is in progress.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def featured_attachment_changed(self, build_id: int) -> None:
        """Clear the old generated link and queue its replacement.

        The caller must already hold the build-row lock used by publication.
        """
        session = self._session()
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

    async def get_render(
        self, schematic_id: int, recipe_hash: str
    ) -> tuple[StoredRender, SchematicPreviewObject] | None:
        session = self._session()
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
        if preview_object is None or preview_object.cleanup_claim_token is not None or preview_object.ready_at is None:
            return None
        return _stored_render(row), preview_object

    async def reserve_preview_object(
        self,
        object_key: str,
        *,
        byte_size: int,
        sha256: str,
    ) -> SchematicPreviewObject:
        """Reserve upload metadata, rejecting objects currently claimed for cleanup.

        Metadata for a referenced object is immutable. An unreferenced object can be reserved with
        new metadata, which clears readiness and requires a new upload.
        """
        session = self._session()
        seen_at = now()
        await session.execute(
            insert(SchematicPreviewObject)
            .values(object_key=object_key, byte_size=byte_size, sha256=sha256, last_seen_at=seen_at)
            .on_conflict_do_nothing(index_elements=[SchematicPreviewObject.object_key])
        )
        row = (
            await session.scalars(
                select(SchematicPreviewObject).where(SchematicPreviewObject.object_key == object_key).with_for_update()
            )
        ).one()
        if row.cleanup_claim_token is not None:
            raise PreviewCleanupInProgressError(object_key)
        referenced = bool(
            await session.scalar(
                select(func.count()).select_from(SchematicRender).where(SchematicRender.object_key == object_key)
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
        return row

    async def mark_preview_object_ready(self, reservation: PreviewObjectReservation) -> None:
        """Record readiness after the caller verifies the upload.

        Check that the reservation still matches and no cleanup claim exists. This method does not
        inspect object storage.
        """
        ready_at = now()
        session = self._session()
        row = await session.scalar(
            select(SchematicPreviewObject)
            .where(SchematicPreviewObject.object_key == reservation.object_key)
            .with_for_update()
        )
        if (
            row is None
            or row.cleanup_claim_token is not None
            or row.byte_size != reservation.byte_size
            or row.sha256 not in (None, reservation.sha256)
        ):
            msg = "Schematic preview object reservation no longer matches its uploaded bytes."
            raise DataIntegrityError(msg, context={"object_key": reservation.object_key})
        row.sha256 = reservation.sha256
        row.ready_at = ready_at
        row.last_seen_at = ready_at
        await session.flush()

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
        session = self._session()
        build_id = await session.scalar(select(BuildSchematic.build_id).where(BuildSchematic.id == schematic_id))
        if build_id is None:
            return None
        await session.scalar(select(Build.id).where(Build.id == build_id).with_for_update())
        preview_object = await session.scalar(
            select(SchematicPreviewObject).where(SchematicPreviewObject.object_key == object_key).with_for_update()
        )
        if (
            preview_object is None
            or preview_object.cleanup_claim_token is not None
            or preview_object.ready_at is None
            or preview_object.byte_size != byte_size
        ):
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
        await session.flush()
        assert row is not None
        return _stored_render(row)

    async def publish_cached_preview(self, schematic_id: int, recipe_hash: str, url: str) -> bool:
        """Publish an existing recipe only if its source remains featured."""
        session = self._session()
        build_id = await session.scalar(select(BuildSchematic.build_id).where(BuildSchematic.id == schematic_id))
        if build_id is None:
            return False
        await session.scalar(select(Build.id).where(Build.id == build_id).with_for_update())
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
        if preview_object is None or preview_object.cleanup_claim_token is not None or preview_object.ready_at is None:
            return False
        await self.replace_generated_preview_link(session, build_id, url)
        await session.flush()
        return True

    @staticmethod
    async def replace_generated_preview_link(
        session: AsyncSession,
        build_id: int,
        url: str,
        *,
        previous_url: str | None = None,
    ) -> None:
        """Replace links owned by registered previews and increment the build revision if changed.

        Preserve other render links. The caller must hold the build-row lock; previous_url identifies
        a link whose recipe row has already been updated in this transaction.
        """
        change = BuildChange(operation="schematic", source="generated_preview", actor_system="preview_worker")
        async with record_build_changes(session, change, (build_id,)):
            registered_urls = PostgresSchematicPreviewRepository._registered_preview_urls(build_id)
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
            await session.execute(update(Build).where(Build.id == build_id).values(revision=Build.revision + 1))

    @staticmethod
    async def _clear_generated_preview_link(session: AsyncSession, build_id: int) -> None:
        change = BuildChange(operation="schematic", source="generated_preview", actor_system="preview_worker")
        async with record_build_changes(session, change, (build_id,)):
            registered_urls = PostgresSchematicPreviewRepository._registered_preview_urls(build_id)
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
            await session.execute(update(Build).where(Build.id == build_id).values(revision=Build.revision + 1))

    @staticmethod
    def _registered_preview_urls(build_id: int) -> Select[str]:
        """Return generated preview URLs whose lifecycle this publisher owns."""
        return (
            select(SchematicRender.url)
            .join(BuildSchematic, SchematicRender.build_schematic_id == BuildSchematic.id)
            .where(BuildSchematic.build_id == build_id)
        )

    async def render_object(self, recipe_hash: str) -> SchematicPreviewObject | None:
        return await self._session().scalar(
            select(SchematicPreviewObject)
            .join(SchematicRender, SchematicRender.object_key == SchematicPreviewObject.object_key)
            .where(SchematicRender.recipe_hash == recipe_hash)
            .limit(1)
        )

    async def claim_cleanup(self, *, older_than: Instant) -> PreviewCleanupClaim | None:
        """Reserve one old unreferenced object, clearing readiness while it is being deleted.

        Skip locked rows and allow reclaiming cleanup leases older than five minutes. The caller must
        commit before deleting the object from storage.
        """
        session = self._session()
        row = await session.scalar(
            select(SchematicPreviewObject)
            .where(
                SchematicPreviewObject.last_seen_at < older_than,
                or_(
                    SchematicPreviewObject.cleanup_claimed_at.is_(None),
                    SchematicPreviewObject.cleanup_claimed_at < func.now() - timedelta(minutes=5),
                ),
                ~select(SchematicRender.id)
                .where(SchematicRender.object_key == SchematicPreviewObject.object_key)
                .exists(),
            )
            .order_by(SchematicPreviewObject.last_seen_at, SchematicPreviewObject.object_key)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        token = uuid4()
        row.cleanup_claim_token = token
        row.cleanup_claimed_at = now()
        row.ready_at = None
        return PreviewCleanupClaim(row.object_key, token)

    async def complete_cleanup(self, claim: PreviewCleanupClaim) -> bool:
        """Delete metadata if this cleanup token still owns the object, returning whether it applied."""
        return (
            await self._session().scalar(
                delete(SchematicPreviewObject)
                .where(
                    SchematicPreviewObject.object_key == claim.object_key,
                    SchematicPreviewObject.cleanup_claim_token == claim.token,
                )
                .returning(SchematicPreviewObject.object_key)
            )
            is not None
        )

    async def release_cleanup(self, claim: PreviewCleanupClaim) -> None:
        await self._session().execute(
            update(SchematicPreviewObject)
            .where(
                SchematicPreviewObject.object_key == claim.object_key,
                SchematicPreviewObject.cleanup_claim_token == claim.token,
            )
            .values(cleanup_claim_token=None, cleanup_claimed_at=None)
        )

    async def _preview_object(self, object_key: str) -> SchematicPreviewObject | None:
        session = self._session()
        return await session.get(SchematicPreviewObject, object_key)

    async def mark_object_not_ready(self, object_key: str) -> None:
        session = self._session()
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
