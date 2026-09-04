"""SQLAlchemy persistence adapter for generated schematic previews."""

from typing import cast

from sqlalchemy import Select, Table, and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.artifacts import ArtifactStore
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.models import BuildLink
from squid.schematics.application.previews import StoredRender
from squid.schematics.infrastructure.models import BuildSchematic, SchematicRender, SchematicRenderQueueItem

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
        return _stored_render(row) if row is not None else None

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
            current_id = await session.scalar(
                select(BuildSchematic.id).where(
                    BuildSchematic.build_id == build_id,
                    BuildSchematic.is_primary,
                )
            )
            if current_id != schematic_id:
                return None
            row = await session.scalar(statement)
            await self.replace_generated_preview_link(session, build_id, url)
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
            render_exists = await session.scalar(
                select(SchematicRender.id).where(
                    SchematicRender.build_schematic_id == schematic_id,
                    SchematicRender.recipe_hash == recipe_hash,
                    SchematicRender.url == url,
                )
            )
            if render_exists is None:
                return False
            await self.replace_generated_preview_link(session, build_id, url)
            await session.commit()
        return True

    @staticmethod
    async def replace_generated_preview_link(session: AsyncSession, build_id: int, url: str) -> None:
        """Atomically replace only the build link owned by registered preview artifacts."""
        registered_urls = PostgresSchematicPreviewPublisher._registered_preview_urls(build_id)
        existing = tuple(
            await session.scalars(
                select(BuildLink.url).where(
                    BuildLink.build_id == build_id,
                    BuildLink.media_type == "render",
                    BuildLink.url.in_(registered_urls),
                )
            )
        )
        if existing == (url,):
            return
        await session.execute(
            delete(BuildLink).where(
                BuildLink.build_id == build_id,
                BuildLink.media_type == "render",
                BuildLink.url.in_(registered_urls),
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
                    select(SchematicRender.object_key, SchematicRender.byte_size).where(
                        SchematicRender.recipe_hash == recipe_hash
                    )
                )
            ).first()
        if row is None or row.object_key is None or row.byte_size > max_bytes:
            return None
        return await self._artifacts.get(row.object_key, max_bytes=max_bytes)


def _stored_render(row: SchematicRender) -> StoredRender:
    return StoredRender(
        schematic_id=row.build_schematic_id,
        recipe_hash=row.recipe_hash,
        url=row.url,
        width=row.width,
        height=row.height,
        byte_size=row.byte_size,
    )
