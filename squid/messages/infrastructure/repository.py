"""SQLAlchemy Discord message fact repository."""

from collections.abc import Callable
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.messages.domain import MessageFact
from squid.messages.models import MessageRecord
from squid.settings.infrastructure.repository import ensure_server_settings


class MessageRepository:
    """Read and update stored messages in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def upsert_fact(self, fact: MessageFact) -> None:
        """Insert or refresh the canonical row for one Discord message.

        `observed_at` is only set on insert: it records when the bot first saw the
        message, which a later sighting must not overwrite.
        """
        statement = (
            pg_insert(MessageRecord)
            .values(
                id=fact.id,
                guild_id=fact.guild_id,
                channel_id=fact.channel_id,
                author_id=fact.author_id,
                content=fact.content,
                created_at=fact.created_at,
                observed_at=Instant.now(),
            )
            .on_conflict_do_update(
                index_elements=[MessageRecord.id],
                set_={
                    "guild_id": fact.guild_id,
                    "channel_id": fact.channel_id,
                    "author_id": fact.author_id,
                    "content": fact.content,
                    "created_at": fact.created_at,
                },
            )
        )
        session = self._session()
        await ensure_server_settings(session, (fact.guild_id,))
        await session.scalar(statement.returning(MessageRecord), execution_options={"populate_existing": True})

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool:
        """Refresh stored content, reporting whether the message was known."""
        statement = (
            update(MessageRecord).where(MessageRecord.id == message_id).values(content=content, edited_at=edited_at)
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
            update(MessageRecord)
            .where(MessageRecord.id == message_id, MessageRecord.deleted_at.is_(None))
            .values(deleted_at=deleted_at)
        )
        session = self._session()
        result = cast(CursorResult[Any], await session.execute(statement))
        return bool(result.rowcount)

    async def get_by_id(self, message_id: int) -> MessageRecord | None:
        session = self._session()
        return await session.scalar(select(MessageRecord).where(MessageRecord.id == message_id))
