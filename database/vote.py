"""Communicates vote data with the database."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from postgrest.base_request_builder import APIResponse

from database import DatabaseManager
from database.builds import Build
from database.message import track_message
from database.schema import VoteSessionRecord, VoteKind

if TYPE_CHECKING:
    pass


async def track_vote_session(message: discord.Message, author_id: int, kind: VoteKind, pass_threshold: int, fail_threshold: int, *, build_id: int | None = None) -> int:
    """Track a vote session in the database.

    Args:
        message: The id of the message that started the vote session.
        author_id: The discord id of the author of the vote session.
        kind: The type of vote session.
        pass_threshold: The number of votes required to pass the vote.
        fail_threshold: The number of votes required to fail the vote.

    Returns:
        The id of the vote session.
    """
    db = DatabaseManager()
    response: APIResponse[VoteSessionRecord] = (
        await db.table("vote_sessions")
        .insert(
            {
                "status": "open",
                "author_id": author_id,
                "kind": kind,
                "pass_threshold": pass_threshold,
                "fail_threshold": fail_threshold,
            }
        )
        .execute()
    )
    session_id = response.data[0]["id"]
    await track_message(message, "vote", build_id=build_id, vote_session_id=session_id)
    return session_id


async def track_build_vote_session(vote_session_id: int, proposed_changes: Build) -> None:
    """Track a build vote session in the database.

    Args:
        vote_session_id: The id of the vote session.
        proposed_changes: The proposed changes for the vote session.

    Raises:
        ValueError: If the proposed changes are empty.
    """
    if proposed_changes.id is None:
        raise ValueError("The build proposed for voting has no id.")
    original_build = await Build.from_id(proposed_changes.id)
    if original_build is None:
        raise ValueError("The build proposed for voting does not exist.")

    if proposed_changes == original_build:
        raise ValueError("There are no changes to vote on.")

    db = DatabaseManager()
    await (
        db.table("build_vote_sessions")
        .insert(
            {
                "vote_session_id": vote_session_id,
                "build_id": proposed_changes.id,
                "changes": original_build.diff(proposed_changes),
            }
        )
        .execute()
    )


async def track_delete_log_vote_session(vote_session_id: int, target_message: discord.Message) -> None:
    """Track a delete log vote session in the database.

    Args:
        vote_session_id: The id of the vote session.
        target_message: The message to delete if the vote passes.
    """
    db = DatabaseManager()
    await (
        db.table("delete_log_vote_sessions")
        .insert(
            {
                "vote_session_id": vote_session_id,
                "target_message_id": target_message.id,
                "target_channel_id": target_message.channel.id,
                "target_server_id": target_message.guild.id,  # type: ignore
            }
        )
        .execute()
    )


async def close_vote_session(vote_session_id: int) -> None:
    """Close a vote session in the database.

    Args:
        vote_session_id: The id of the vote session.
    """
    db = DatabaseManager()
    await db.table("vote_sessions").update({"status": "closed"}).eq("id", vote_session_id).execute()


async def upsert_vote(vote_session_id: int, user_id: int, weight: int | None) -> None:
    """Upsert a vote in the database.

    Args:
        vote_session_id: The id of the vote session.
        user_id: The id of the user voting.
        weight: The weight of the vote. None to remove the vote.
    """
    db = DatabaseManager()
    await db.table("votes").upsert({"vote_session_id": vote_session_id, "user_id": user_id, "weight": weight}).execute()
