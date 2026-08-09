"""SQLAlchemy tracked message repository."""

from collections.abc import Sequence
from typing import cast

from advanced_alchemy.exceptions import NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.messages.domain import MessagePurposeLiteral, MessageRecord
from squid.messages.errors import MessageNotFoundError
from squid.messages.infrastructure.models import Message
from squid.persistence.repository import BaseAsyncRepository


class _MessageModelRepository(BaseAsyncRepository[Message]):
    model_type = Message


class MessageRepository:
    """Repository for pure database operations on messages."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def insert(
        self,
        message_id: int,
        server_id: int,
        channel_id: int,
        author_id: int,
        purpose: MessagePurposeLiteral,
        content: str | None,
        *,
        build_id: int | None = None,
        vote_session_id: int | None = None,
    ) -> None:
        """Insert a message record into the database.

        Args:
            message_id: The Discord message ID.
            server_id: The server ID where the message was sent.
            channel_id: The channel ID where the message was sent.
            author_id: The author ID of the message.
            purpose: The purpose of the message.
            content: The content of the message.
            build_id: The associated build id, can be None.
            vote_session_id: The vote session id of the message.
        """
        async with self._session_factory() as session:
            repository = _MessageModelRepository(session=session, auto_commit=True)
            await repository.add(
                Message(
                    id=message_id,
                    server_id=server_id,
                    channel_id=channel_id,
                    author_id=author_id,
                    purpose=purpose,
                    content=content,
                    build_id=build_id,
                    vote_session_id=vote_session_id,
                )
            )

    async def update_edited_time(self, message_id: int) -> None:
        """Update the edited time of a message.

        Args:
            message_id: The message ID to update.
        """
        async with self._session_factory() as session:
            repository = _MessageModelRepository(session=session, auto_commit=True)
            message = await repository.get_one_or_none(id=message_id)
            if message is not None:
                message.updated_at = Instant.now()
                await repository.update(message)

    async def get_by_id(self, message_id: int) -> MessageRecord | None:
        """Get a message by its ID.

        Args:
            message_id: The message ID to retrieve.

        Returns:
            The Message object if found, otherwise None.
        """
        async with self._session_factory() as session:
            repository = _MessageModelRepository(session=session)
            message = await repository.get_one_or_none(id=message_id)
            return None if message is None else self._to_record(message)

    async def delete_by_id(self, message_id: int) -> MessageRecord:
        """Delete a message from the database by ID.

        Args:
            message_id: The message ID to delete.

        Returns:
            The deleted Message object.

        Raises:
            MessageNotFoundError: If the message is not found.
        """
        async with self._session_factory() as session:
            repository = _MessageModelRepository(session=session, auto_commit=True)
            try:
                return self._to_record(await repository.delete(message_id))
            except NotFoundError as exc:
                raise MessageNotFoundError(message_id) from exc

    async def list_for_build(self, build_id: int, author_id: int) -> Sequence[MessageRecord]:
        """Return messages for a build created by one Discord author."""
        stmt = select(Message).where(Message.build_id == build_id, Message.author_id == author_id)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return [self._to_record(message) for message in result.scalars().all()]

    async def list_for_build_purpose(self, build_id: int, purpose: MessagePurposeLiteral) -> Sequence[MessageRecord]:
        """Return every tracked message serving one purpose for a build."""
        stmt = select(Message).where(Message.build_id == build_id, Message.purpose == purpose)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return [self._to_record(message) for message in result.scalars().all()]

    @staticmethod
    def _to_record(message: Message) -> MessageRecord:
        return MessageRecord(
            id=message.id,
            server_id=message.server_id,
            channel_id=message.channel_id,
            author_id=message.author_id,
            purpose=cast(MessagePurposeLiteral, message.purpose),
            content=message.content,
            build_id=message.build_id,
            vote_session_id=message.vote_session_id,
            updated_at=message.updated_at,
        )
