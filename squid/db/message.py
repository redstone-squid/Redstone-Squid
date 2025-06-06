"""Some functions related to the message table, which stores message ids."""

import discord
from postgrest.base_request_builder import APIResponse, SingleAPIResponse

from squid.db.schema import MessagePurpose, MessageRecord
from squid.db.utils import utcnow
from supabase import AsyncClient


class MessageManager:
    """A class for managing messages in the database."""

    def __init__(self, client: AsyncClient):
        self.client = client

    async def track_message(
        self,
        message: discord.Message,
        purpose: MessagePurpose,
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

        await (
            self.client.table("messages")
            .insert(
                {
                    "server_id": message.guild.id,
                    "channel_id": message.channel.id,
                    "id": message.id,
                    "build_id": build_id,
                    "author_id": message.author.id,
                    "vote_session_id": vote_session_id,
                    "purpose": purpose,
                }
            )
            .execute()
        )

    async def update_message_edited_time(self, message: int | discord.Message) -> None:
        """
        Update the edited time of a message.

        Args:
            message: The message to update. Either the message id or the message object.
        """
        message_id = message.id if isinstance(message, discord.Message) else message
        await self.client.table("messages").update({"edited_time": utcnow()}).eq("id", message_id).execute()

    async def untrack_message(self, message: int | discord.Message) -> MessageRecord:
        """Untrack message from the database. The message is not deleted on discord.

        Args:
            message: The message to untrack. Either the message id or the message object.

        Returns:
            A MessageRecord that is untracked.

        Raises:
            ValueError: If the message is not found.
        """
        message_id = message.id if isinstance(message, discord.Message) else message
        response: APIResponse[MessageRecord] = (
            await self.client.table("messages").delete().eq("id", message_id).execute()
        )
        if response.data:
            return response.data[0]
        else:
            raise ValueError(f"Message with id {message_id} not found.")

    async def get_outdated_messages(self, server_id: int) -> list[MessageRecord] | None:
        """Returns a list of messages that are outdated.

        Args:
            server_id: The server id to check for outdated messages.

        Returns:
            A list of messages.
        """
        # Messages that have been updated since the last submission message update.
        response: SingleAPIResponse[list[MessageRecord]] = await self.client.rpc(
            "get_outdated_messages", {"server_id_input": server_id}
        ).execute()
        server_outdated_messages = response.data
        return server_outdated_messages


async def main():
    from squid.db import DatabaseManager

    # print(get_outdated_message(433618741528625152, 30))
    print(await DatabaseManager().message.get_outdated_messages(433618741528625153))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
