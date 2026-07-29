"""SQLAlchemy tracked message repository."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from advanced_alchemy.exceptions import NotFoundError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.repos._model_repos import MessageModelRepository
from squid.db.schema import Message
from squid.exceptions import MessageNotFoundError
from squid.messages.domain import MessagePurposeLiteral, MessageRecord


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
            repository = MessageModelRepository(session=session, auto_commit=True)
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
            repository = MessageModelRepository(session=session, auto_commit=True)
            message = await repository.get_one_or_none(id=message_id)
            if message is not None:
                message.updated_at = datetime.now(tz=UTC)
                await repository.update(message)

    async def get_by_id(self, message_id: int) -> MessageRecord | None:
        """Get a message by its ID.

        Args:
            message_id: The message ID to retrieve.

        Returns:
            The Message object if found, otherwise None.
        """
        async with self._session_factory() as session:
            repository = MessageModelRepository(session=session)
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
            repository = MessageModelRepository(session=session, auto_commit=True)
            try:
                return self._to_record(await repository.delete(message_id))
            except NotFoundError as exc:
                raise MessageNotFoundError(message_id) from exc

    async def get_outdated_messages(self, server_id: int) -> Sequence[MessageRecord]:
        """Get outdated messages by calling the PostgreSQL function.

        Args:
            server_id: The server ID to check for outdated messages.

        Returns:
            A sequence of outdated Message objects.
        """
        # Call the PostgreSQL function that returns SETOF messages
        # Since the function returns records matching the messages table,
        # we can select from it and map to Message objects
        stmt = select(Message).from_statement(select(func.get_outdated_messages(server_id)))
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
