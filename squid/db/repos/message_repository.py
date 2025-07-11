"""Repository for managing messages in the database."""

from collections.abc import Sequence

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import Message, MessagePurposeLiteral
from squid.utils import utcnow


class MessageRepository:
    """Repository for pure database operations on messages."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self._session = session

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
        async with self._session() as session:
            stmt = insert(Message).values(
                id=message_id,
                server_id=server_id,
                channel_id=channel_id,
                author_id=author_id,
                purpose=purpose,
                content=content,
                build_id=build_id,
                vote_session_id=vote_session_id,
            )
            await session.execute(stmt)
            await session.commit()

    async def update_edited_time(self, message_id: int) -> None:
        """Update the edited time of a message.

        Args:
            message_id: The message ID to update.
        """
        async with self._session() as session:
            stmt = update(Message).where(Message.id == message_id).values(edited_time=utcnow())
            await session.execute(stmt)
            await session.commit()

    async def get_by_id(self, message_id: int) -> Message | None:
        """Get a message by its ID.

        Args:
            message_id: The message ID to retrieve.

        Returns:
            The Message object if found, otherwise None.
        """
        async with self._session() as session:
            stmt = select(Message).where(Message.id == message_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def delete_by_id(self, message_id: int) -> Message | None:
        """Delete a message from the database by ID.

        Args:
            message_id: The message ID to delete.

        Returns:
            The deleted Message object.
        """
        async with self._session() as session:
            stmt = select(Message).where(Message.id == message_id)
            result = await session.execute(stmt)
            message_obj = result.scalar_one_or_none()

            if message_obj is None:
                return None

            await session.delete(message_obj)
            await session.commit()
            return message_obj

    async def get_outdated_messages(self, server_id: int) -> Sequence[Message]:
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
        async with self._session() as session:
            result = await session.execute(stmt)
            return result.scalars().all()
