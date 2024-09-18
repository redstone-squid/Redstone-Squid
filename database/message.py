"""Some functions related to the message table, which stores message ids."""

from __future__ import annotations

from postgrest.base_request_builder import APIResponse, SingleAPIResponse
from postgrest.types import CountMethod

from database.builds import Build, get_builds
from database.schema import MessageRecord
from database.utils import utcnow
from database import DatabaseManager


# TODO: Find better names for these functions, the "message" is not really a discord message, but a record in the database.
async def get_server_messages(server_id: int) -> list[MessageRecord]:
    """Get all tracked bot messages in a server."""
    response: APIResponse[MessageRecord] = (
        await DatabaseManager().table("messages").select("*").eq("server_id", server_id).execute()
    )
    return response.data


async def get_build_messages(build_id: int) -> list[MessageRecord]:
    """Get all messages for a build."""
    response: APIResponse[MessageRecord] = (
        await DatabaseManager().table("messages").select("*").eq("build_id", build_id).execute()
    )
    return response.data


async def get_messages(server_id: int, build_id: int) -> list[MessageRecord]:
    """Get the unique message for a build in a server"""
    db = DatabaseManager()
    server_record: APIResponse[MessageRecord] = (
        await db.table("messages").select("*").eq("server_id", server_id).eq("build_id", build_id).execute()
    )
    return server_record.data


async def add_message(
    server_id: int, build_id: int, channel_id: int, message_id: int, purpose: str
) -> None:
    """Add a message to the database."""
    await (
        DatabaseManager()
        .table("messages")
        .insert(
            {
                "server_id": server_id,
                "build_id": build_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "edited_time": utcnow(),
                "purpose": purpose,
            }
        )
        .execute()
    )


async def update_message_edited_time(message_id: int) -> None:
    """Update the edited time of a message."""
    await DatabaseManager().table("messages").update({"edited_time": utcnow()}).eq("message_id", message_id).execute()


async def delete_message(server_id: int, build_id: int) -> list[int]:
    """Remove a message from the database.

    Args:
        server_id: The server id of the message to delete.
        build_id: The build id of the message to delete.

    Returns:
        A list of message ids that were deleted.
    """
    db = DatabaseManager()
    response: APIResponse[MessageRecord] = (
        await db.table("messages")
        .select("message_id", count=CountMethod.exact)
        .eq("server_id", server_id)
        .eq("build_id", build_id)
        .execute()
    )
    if not response.count:
        return []
    message_ids = [response.data[i]["message_id"] for i in range(response.count)]
    await db.table("messages").delete().in_("message_id", message_ids).execute()
    return message_ids


async def get_outdated_messages(server_id: int) -> list[MessageRecord] | None:
    """Returns a list of messages that are outdated.

    Args:
        server_id: The server id to check for outdated messages.

    Returns:
        A list of messages.
    """
    db = DatabaseManager()
    # Messages that have been updated since the last submission message update.
    response = await db.rpc("get_outdated_messages", {"server_id_input": server_id}).execute()
    server_outdated_messages = response.data
    return server_outdated_messages


async def get_unsent_builds(server_id: int) -> list[Build]:
    """
    Gets all the builds without messages in this server.

    Args:
        server_id: The server id to check for.

    Returns:
        A list of messages
    """
    db = DatabaseManager()
    response = await db.rpc("get_unsent_builds", {"server_id_input": server_id}).execute()
    build_ids = [row["id"] for row in response.data]
    builds = await get_builds(build_ids)
    return [build for build in builds if build is not None]


async def get_build_id_by_message(message_id: int) -> int | None:
    """
    Get the build id by the message id.

    Args:
        message_id: The message id to get the build id from.

    Returns:
        The build id of the message.
    """
    db = DatabaseManager()
    response: SingleAPIResponse[MessageRecord] | None = (
        await db.table("messages")
        .select("build_id", count=CountMethod.exact)
        .eq("message_id", message_id)
        .maybe_single()
        .execute()
    )
    return response.data["build_id"] if response else None


if __name__ == "__main__":
    # print(get_outdated_message(433618741528625152, 30))
    # print(get_outdated_messages(433618741528625152))
    print(get_build_id_by_message(536004554743873556))
