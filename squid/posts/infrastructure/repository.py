"""SQLAlchemy adapter for bot-owned Discord posts."""

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.posts.domain import PostReference, ResourceKind, Surface
from squid.posts.models import DiscordPost
from squid.sync.models import DiscordSyncQueueItem


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
        """Insert a sent post, leaving an existing message ID unchanged.

        A different live post for the same resource and channel raises IntegrityError.
        """
        statement = (
            pg_insert(DiscordPost)
            .values(
                message_id=message_id,
                channel_id=channel_id,
                resource_kind=resource_kind,
                resource_key=resource_key,
                surface=surface,
                applied_revision=applied_revision,
                posted_at=Instant.now(),
            )
            .on_conflict_do_nothing(index_elements=[DiscordPost.message_id])
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]:
        statement = (
            select(DiscordPost)
            .where(
                DiscordPost.resource_kind == resource_kind,
                DiscordPost.resource_key == resource_key,
            )
            .order_by(DiscordPost.channel_id)
        )
        async with self._session_factory() as session:
            return (await session.scalars(statement)).all()

    async def resolve(self, message_id: int) -> PostReference | None:
        statement = select(DiscordPost).where(DiscordPost.message_id == message_id)
        async with self._session_factory() as session:
            row = await session.scalar(statement)
        if row is None:
            return None
        return PostReference(
            message_id=row.message_id,
            resource_kind=row.resource_kind,
            resource_key=row.resource_key,
            surface=row.surface,
        )

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None:
        """Record a newer rendered revision and its timestamp."""
        statement = (
            update(DiscordPost)
            .where(
                DiscordPost.message_id == message_id,
                DiscordPost.applied_revision < applied_revision,
            )
            .values(applied_revision=applied_revision, rendered_at=Instant.now())
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def mark_applied(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None:
        statement = (
            update(DiscordPost)
            .where(
                DiscordPost.resource_kind == resource_kind,
                DiscordPost.resource_key == resource_key,
                DiscordPost.applied_revision < generation,
            )
            .values(applied_revision=generation, rendered_at=Instant.now())
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def suppress(self, message_id: int) -> bool:
        """Mark a live post as suppressed, returning True if it changed."""
        statement = (
            update(DiscordPost)
            .where(DiscordPost.message_id == message_id, DiscordPost.suppressed_at.is_(None))
            .values(suppressed_at=Instant.now())
        )
        async with self._session_factory.begin() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            return bool(result.rowcount)

    async def forget(self, message_id: int) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(delete(DiscordPost).where(DiscordPost.message_id == message_id))

    async def pending_generation(self, resource_kind: ResourceKind, resource_key: str) -> int | None:
        """Return the queued generation if a live post is behind, otherwise None."""
        statement = (
            select(DiscordSyncQueueItem.generation)
            .where(
                DiscordSyncQueueItem.resource_kind == resource_kind,
                DiscordSyncQueueItem.source_key == resource_key,
                DiscordSyncQueueItem.generation
                > select(func.coalesce(func.min(DiscordPost.applied_revision), -1))
                .where(
                    DiscordPost.resource_kind == resource_kind,
                    DiscordPost.resource_key == resource_key,
                    DiscordPost.suppressed_at.is_(None),
                )
                .scalar_subquery(),
            )
            .limit(1)
        )
        async with self._session_factory() as session:
            return await session.scalar(statement)
