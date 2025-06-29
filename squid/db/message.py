"""Some functions related to the message table, which stores message ids."""

from collections.abc import Sequence

import discord

from squid.db import MessageRepository
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
            raise NotImplementedError("Cannot track messages in DMs.")  # TODO

        if purpose in ["view_pending_build", "confirm_pending_build"] and build_id is None:
            raise ValueError("build_id cannot be None for this purpose.")
        elif purpose == "vote" and vote_session_id is None:
            raise ValueError("vote_session_id cannot be None for this purpose.")

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

    async def update_message_edited_time(self, message: int | discord.Message) -> None:
        """Update the edited time of a message.

        Args:
            message: The message to update. Either the message id or the message object.
        """
        message_id = message.id if isinstance(message, discord.Message) else message
        await self._message_repo.update_edited_time(message_id)

    async def untrack_message(self, message: int | discord.Message) -> Message:
        """Untrack message from the database. The message is not deleted on discord.

        Args:
            message: The message to untrack. Either the message id or the message object.

        Returns:
            A Message that is untracked.

        Raises:
            ValueError: If the message is not found in the database.
        """
        message_id = message.id if isinstance(message, discord.Message) else message
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


async def main():
    from squid.db import DatabaseManager

    # print(get_outdated_message(433618741528625152, 30))
    print(await DatabaseManager().message.get_outdated_messages(433618741528625153))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
