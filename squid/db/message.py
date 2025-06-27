"""Some functions related to the message table, which stores message ids."""

from collections.abc import Sequence

import discord
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import Message, MessagePurposeLiteral
from squid.db.utils import utcnow


class MessageManager:
    """A class for managing messages in the database."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self.session = session

    async def track_message(
        self,
        message: discord.Message,
        purpose: MessagePurposeLiteral,
        *,
        build_id: int | None = None,
        vote_session_id: int | None = None,
    ) -> None:
        """Track a message in the database.

        Args:
            message: The message to track.
            build_id: The associated build id, can be None.
            purpose: The purpose of the message.
            vote_session_id: The vote session id of the message.
        """
        if message.guild is None:
            raise NotImplementedError("Cannot track messages in DMs.")  # TODO

        if purpose in ["view_pending_build", "confirm_pending_build"] and build_id is None:
            raise ValueError("build_id cannot be None for this purpose.")
        elif purpose == "vote" and vote_session_id is None:
            raise ValueError("vote_session_id cannot be None for this purpose.")

        async with self.session() as session:
            stmt = insert(Message).values(
                server_id=message.guild.id,
                channel_id=message.channel.id,
                id=message.id,
                build_id=build_id,
                author_id=message.author.id,
                vote_session_id=vote_session_id,
                purpose=purpose,
                content=message.content,
            )
            await session.execute(stmt)
            await session.commit()

    async def update_message_edited_time(self, message: int | discord.Message) -> None:
        """
        Update the edited time of a message.

        Args:
            message: The message to update. Either the message id or the message object.
        """
        message_id = message.id if isinstance(message, discord.Message) else message
        async with self.session() as session:
            stmt = update(Message).where(Message.id == message_id).values(edited_time=utcnow())
            await session.execute(stmt)
            await session.commit()

    async def untrack_message(self, message: int | discord.Message) -> Message:
        """Untrack message from the database. The message is not deleted on discord.

        Args:
            message: The message to untrack. Either the message id or the message object.

        Returns:
            A Message that is untracked.

        Raises:
            ValueError: If the message is not found.
        """
        message_id = message.id if isinstance(message, discord.Message) else message
        async with self.session() as session:
            stmt = select(Message).where(Message.id == message_id)
            result = await session.execute(stmt)
            message_obj = result.scalar_one_or_none()

            if message_obj is None:
                raise ValueError(f"Message with id {message_id} not found.")

            await session.delete(message_obj)
            await session.commit()
            return message_obj

    async def get_outdated_messages(self, server_id: int) -> Sequence[Message]:
        """Returns a list of messages that are outdated.

        Args:
            server_id: The server id to check for outdated messages.

        Returns:
            A list of messages.
        """
        async with self.session() as session:
            stmt = text("SELECT * FROM get_outdated_messages(:server_id_input)")
            result = await session.execute(stmt, {"server_id_input": server_id})
            rows = result.scalars().fetchall()
            return rows
