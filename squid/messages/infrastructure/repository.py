"""SQLAlchemy repository for stored Discord messages."""

from collections.abc import Callable
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.messages.domain import MessageSnapshot
from squid.messages.models import Message
from squid.settings.infrastructure.repository import ensure_server_settings


class MessageRepository:
    """Read and update stored messages in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def upsert_snapshot(self, snapshot: MessageSnapshot) -> None:
        """Insert or refresh the canonical row for one Discord message.

        `observed_at` is only set on insert: it records when the bot first saw the
        message, which a later sighting must not overwrite.
        """
        assert snapshot.author_id is not None, "A message must record its author."
        statement = (
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
            .on_conflict_do_update(
                index_elements=[Message.id],
                set_={
                    "guild_id": snapshot.guild_id,
                    "channel_id": snapshot.channel_id,
                    "author_id": snapshot.author_id,
                    "content": snapshot.content,
                    "created_at": snapshot.created_at,
                },
            )
        )
        session = self._session()
        await ensure_server_settings(session, (snapshot.guild_id,))
        await session.scalar(statement.returning(Message), execution_options={"populate_existing": True})

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool:
        """Refresh stored content, reporting whether the message was known."""
        statement = update(Message).where(Message.id == message_id).values(content=content, edited_at=edited_at)
        session = self._session()
        result = cast(CursorResult[Any], await session.execute(statement))
        return bool(result.rowcount)

    async def refresh_content(self, message_id: int, content: str | None, edited_at: Instant | None) -> bool:
        """Store content read from Discord if it differs, reporting whether a row changed.

        A None `edited_at` keeps the stored edit time, since Discord reports no edit.
        """
        statement = (
            update(Message)
            .where(Message.id == message_id, Message.content.is_distinct_from(content))
            .values(
                content=content,
                edited_at=Message.edited_at if edited_at is None else edited_at,
            )
        )
        session = self._session()
        result = cast(CursorResult[Any], await session.execute(statement))
        return bool(result.rowcount)

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool:
        """Set the deletion time only if the message has not already been marked deleted.

        Return True if a row changed. Unknown messages and repeated deletion
        notices return False, preserving the original deletion time.
        """
        statement = (
            update(Message).where(Message.id == message_id, Message.deleted_at.is_(None)).values(deleted_at=deleted_at)
        )
        session = self._session()
        result = cast(CursorResult[Any], await session.execute(statement))
        return bool(result.rowcount)

    async def get_by_id(self, message_id: int) -> Message | None:
        session = self._session()
        return await session.scalar(select(Message).where(Message.id == message_id))
