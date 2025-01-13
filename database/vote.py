"""Communicates vote data with the database."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, TypeVar

import discord
from postgrest.base_request_builder import APIResponse

from database import DatabaseManager
from database.message import track_message
from database.schema import VoteSessionRecord, VoteKind

if TYPE_CHECKING:
    pass


T = TypeVar("T")


async def track_vote_session(
    messages: Iterable[discord.Message],
    author_id: int,
    kind: VoteKind,
    pass_threshold: int,
    fail_threshold: int,
    *,
    build_id: int | None = None,
) -> int:
    """Track a vote session in the database.

    Args:
        messages: The messages belonging to the vote session.
        author_id: The discord id of the author of the vote session.
        kind: The type of vote session.
        pass_threshold: The number of votes required to pass the vote.
        fail_threshold: The number of votes required to fail the vote.
        build_id: The id of the build to vote on. None if the vote is not about a build.

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
    coros = [track_message(message, "vote", build_id=build_id, vote_session_id=session_id) for message in messages]
    await asyncio.gather(*coros)
    return session_id


async def track_build_vote_session(vote_session_id: int, build_id: int, changes: list[tuple[str, T, T]]) -> None:
    """Track a build vote session in the database.

    Args:
        vote_session_id: The id of the vote session.
        build_id: The id of the build to vote on.
        changes: The proposed changes for the vote session.

    Raises:
        ValueError: If the proposed changes are empty.
    """
    db = DatabaseManager()
    await (
        db.table("build_vote_sessions")
        .insert(
            {
                "vote_session_id": vote_session_id,
                "build_id": build_id,
                "changes": changes,
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


async def upsert_vote(vote_session_id: int, user_id: int, weight: float | None) -> None:
    """Upsert a vote in the database.

    Args:
        vote_session_id: The id of the vote session.
        user_id: The id of the user voting.
        weight: The weight of the vote. None to remove the vote.
    """
    db = DatabaseManager()
    await db.table("votes").upsert({"vote_session_id": vote_session_id, "user_id": user_id, "weight": weight}).execute()
