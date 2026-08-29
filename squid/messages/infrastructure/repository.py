"""SQLAlchemy Discord message fact repository."""

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.messages.domain import MessageFact, MessageRecord
from squid.messages.infrastructure.models import Message


class MessageRepository:
    """Repository for pure database operations on Discord message facts."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def upsert_fact(self, fact: MessageFact) -> None:
        """Insert or refresh the canonical row for one Discord message.

        `observed_at` is only set on insert: it records when the bot first saw the
        message, which a later sighting must not overwrite.
        """
        statement = (
            pg_insert(Message)
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
                index_elements=[Message.id],
                set_={
                    "guild_id": fact.guild_id,
                    "channel_id": fact.channel_id,
                    "author_id": fact.author_id,
                    "content": fact.content,
                    "created_at": fact.created_at,
                },
            )
        )
        async with self._session_factory.begin() as session:
            await session.execute(statement)

    async def record_edit(self, message_id: int, content: str | None, edited_at: Instant) -> bool:
        """Refresh stored content, reporting whether the message was known."""
        statement = update(Message).where(Message.id == message_id).values(content=content, edited_at=edited_at)
        async with self._session_factory.begin() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            return bool(result.rowcount)

    async def mark_deleted(self, message_id: int, deleted_at: Instant) -> bool:
        """Tombstone a message, reporting whether it was known.

        Only the first deletion wins, so a redelivered raw event cannot move the
        timestamp forward.
        """
        statement = (
            update(Message).where(Message.id == message_id, Message.deleted_at.is_(None)).values(deleted_at=deleted_at)
        )
        async with self._session_factory.begin() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            return bool(result.rowcount)

    async def get_by_id(self, message_id: int) -> MessageRecord | None:
        async with self._session_factory() as session:
            message = await session.scalar(select(Message).where(Message.id == message_id))
        return None if message is None else self._to_record(message)

    @staticmethod
    def _to_record(message: Message) -> MessageRecord:
        return MessageRecord(
            id=message.id,
            channel_id=message.channel_id,
            author_id=message.author_id,
            guild_id=message.guild_id,
            content=message.content,
            created_at=message.created_at,
            observed_at=message.observed_at,
            edited_at=message.edited_at,
            deleted_at=message.deleted_at,
        )
