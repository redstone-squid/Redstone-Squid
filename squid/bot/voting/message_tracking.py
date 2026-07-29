"""Persistence helpers for Discord vote messages."""

import asyncio
from collections.abc import Iterable

import discord

from squid.bot.message_adapter import to_tracked_message
from squid.messages.application import MessageService


async def track_vote_messages(
    messages: Iterable[discord.Message],
    message_service: MessageService,
    vote_session_id: int,
    *,
    build_id: int | None = None,
) -> None:
    """Associate Discord messages with a persisted vote session.

    Args:
        messages: The messages belonging to the vote session.
        message_service: Application service for tracked Discord messages.
        vote_session_id: The persisted vote session identifier.
        build_id: The id of the build to vote on. None if the vote is not about a build.
    """
    coros = [
        message_service.track(
            to_tracked_message(message),
            "vote",
            build_id=build_id,
            vote_session_id=vote_session_id,
        )
        for message in messages
    ]
    await asyncio.gather(*coros)
