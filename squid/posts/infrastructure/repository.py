"""SQLAlchemy adapter for bot-owned Discord posts."""

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.posts.domain import DiscordPost, PostReference, ResourceKind, Surface
from squid.posts.infrastructure.models import DiscordPost as SQLDiscordPost
from squid.sync.infrastructure.models import DiscordSyncQueueItem


class PostRepository:
    """Database operations on the bot's own rendered Discord messages."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def record(
        self,
        *,
        message_id: int,
        channel_id: int,
        resource_kind: ResourceKind,
        resource_key: str,
        surface: Surface,
        applied_revision: int,
    ) -> None:
        """Claim a sent message as this resource's post, tolerating an exact replay.

        A retry that already sent and already recorded is the same row, so it is a
        no-op rather than a conflict. A *different* message for the same resource and
        channel still violates the unique index, which is the duplicate-card guard.
        """
        statement = (
            pg_insert(SQLDiscordPost)
            .values(
                message_id=message_id,
                channel_id=channel_id,
                resource_kind=resource_kind,
                resource_key=resource_key,
                surface=surface,
                applied_revision=applied_revision,
                posted_at=Instant.now(),
            )
            .on_conflict_do_nothing(index_elements=[SQLDiscordPost.message_id])
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]:
        statement = (
            select(SQLDiscordPost)
            .where(
                SQLDiscordPost.resource_kind == resource_kind,
                SQLDiscordPost.resource_key == resource_key,
            )
            .order_by(SQLDiscordPost.channel_id)
        )
        async with self._session_factory() as session:
            return [self._to_domain(row) for row in (await session.scalars(statement)).all()]

    async def resolve(self, message_id: int) -> PostReference | None:
        statement = select(SQLDiscordPost).where(SQLDiscordPost.message_id == message_id)
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        if row is None:
            return None
        return PostReference(
            message_id=row.message_id,
            resource_kind=cast(ResourceKind, row.resource_kind),
            resource_key=row.resource_key,
            surface=cast(Surface, row.surface),
        )

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None:
        """Advance one post's applied revision, never backwards.

        Two reconciler passes can overlap, and the slower one must not report an older
        generation as the current state.
        """
        statement = (
            update(SQLDiscordPost)
            .where(
                SQLDiscordPost.message_id == message_id,
                SQLDiscordPost.applied_revision < applied_revision,
            )
            .values(applied_revision=applied_revision, rendered_at=Instant.now())
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def mark_applied(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None:
        statement = (
            update(SQLDiscordPost)
            .where(
                SQLDiscordPost.resource_kind == resource_kind,
                SQLDiscordPost.resource_key == resource_key,
                SQLDiscordPost.applied_revision < generation,
            )
            .values(applied_revision=generation, rendered_at=Instant.now())
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def suppress(self, message_id: int) -> bool:
        """Tombstone a post deleted outside the bot, reporting whether one matched."""
        statement = (
            update(SQLDiscordPost)
            .where(SQLDiscordPost.message_id == message_id, SQLDiscordPost.suppressed_at.is_(None))
            .values(suppressed_at=Instant.now())
        )
        async with self._session_factory.begin() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            return bool(result.rowcount)

    async def forget(self, message_id: int) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(delete(SQLDiscordPost).where(SQLDiscordPost.message_id == message_id))

    async def pending_generation(self, resource_kind: ResourceKind, resource_key: str) -> int | None:
        """Return the queued generation this resource's posts have not reached yet.

        Desired state lives only on the queue row, so staleness is a join rather than a
        revision copied onto every post.
        """
        statement = (
            select(DiscordSyncQueueItem.generation)
            .where(
                DiscordSyncQueueItem.resource_kind == resource_kind,
                DiscordSyncQueueItem.source_key == resource_key,
                DiscordSyncQueueItem.generation
                > select(func.coalesce(func.min(SQLDiscordPost.applied_revision), -1))
                .where(
                    SQLDiscordPost.resource_kind == resource_kind,
                    SQLDiscordPost.resource_key == resource_key,
                    SQLDiscordPost.suppressed_at.is_(None),
                )
                .scalar_subquery(),
            )
            .limit(1)
        )
        async with self._session_factory() as session:
            return await session.scalar(statement)

    @staticmethod
    def _to_domain(row: SQLDiscordPost) -> DiscordPost:
        return DiscordPost(
            message_id=row.message_id,
            channel_id=row.channel_id,
            resource_kind=cast(ResourceKind, row.resource_kind),
            resource_key=row.resource_key,
            surface=cast(Surface, row.surface),
            applied_revision=row.applied_revision,
            posted_at=row.posted_at,
            rendered_at=row.rendered_at,
            suppressed_at=row.suppressed_at,
        )
