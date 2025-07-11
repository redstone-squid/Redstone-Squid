"""Some functions related to the message table, which stores message ids."""

from collections.abc import Sequence
from typing import cast

import discord
from sqlalchemy import select

from squid.db.repos.message_repository import MessageRepository
from squid.db.schema import Message, MessagePurposeLiteral


class MessageService:
    """Service for managing messages in the database."""

    def __init__(self, message_repo: MessageRepository):
        self._message_repo = message_repo

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
            purpose: The purpose of the message.
            build_id: The associated build id, can be None.
            vote_session_id: The vote session id of the message.

        Raises:
            NotImplementedError: If trying to track messages in DMs.
            ValueError: If required parameters are missing for specific purposes.
        """
        if message.guild is None:
            msg = "Cannot track messages in DMs."
            raise NotImplementedError(msg)  # TODO

        if purpose in ["view_pending_build", "confirm_pending_build"] and build_id is None:
            msg = "build_id cannot be None for this purpose."
            raise ValueError(msg)
        if purpose == "vote" and vote_session_id is None:
            msg = "vote_session_id cannot be None for this purpose."
            raise ValueError(msg)

        await self._message_repo.insert(
            message_id=message.id,
            server_id=message.guild.id,
            channel_id=message.channel.id,
            author_id=message.author.id,
            purpose=purpose,
            content=message.content,
            build_id=build_id,
            vote_session_id=vote_session_id,
        )

    async def get_message_by_id(self, message_id: int) -> Message | None:
        """Get a message from the database.

        Returns:
            The Message object if found, otherwise None.
        """
        async with self.session() as session:
            stmt = select(Message).where(Message.id == message_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_messages_by_id(self, message_ids: Sequence[int]) -> Sequence[Message]:
        """Get multiple messages from the database.

        Returns:
            A list of Message objects.
        """
        async with self.session() as session:
            stmt = select(Message).where(Message.id.in_(message_ids))
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_message_edited_time(self, message: int | discord.Message) -> None:
        """Update the edited time of a message.

        Args:
            message: The message to update. Either the message id or the message object.
        """
        message_id = message.id if isinstance(message, discord.Message) else cast(int, message)
        await self._message_repo.update_edited_time(message_id)

    async def untrack_message(self, message: int | discord.Message) -> Message | None:
        """Untrack message from the database. The message is not deleted on discord.

        Args:
            message: The message to untrack. Either the message id or the message object.

        Returns:
            A Message that is untracked, or None if the message was not found in the database.
        """
        message_id = message.id if isinstance(message, discord.Message) else cast(int, message)
        return await self._message_repo.delete_by_id(message_id)

    async def get_by_id(self, message_id: int) -> Message | None:
        """Get a message by its ID.

        Args:
            message_id: The message ID to retrieve.

        Returns:
            The Message object if found, otherwise None.
        """
        return await self._message_repo.get_by_id(message_id)

    async def get_outdated_messages(self, server_id: int) -> Sequence[Message]:
        """Returns a list of messages that are outdated.

        Args:
            server_id: The server id to check for outdated messages.

        Returns:
            A list of messages.
        """
        return await self._message_repo.get_outdated_messages(server_id)
