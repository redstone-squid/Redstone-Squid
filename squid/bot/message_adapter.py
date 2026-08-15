"""Conversions between Discord messages and application message metadata."""

import discord
from whenever import Instant

from squid.messages.domain import MessageFact, TrackedMessage


def to_message_fact(message: discord.Message) -> MessageFact:
    """Convert a Discord message to the framework-neutral fact recorded for it.

    Unlike :func:`to_tracked_message` this accepts DMs: a fact is true regardless of
    where the message lives, and only the guild is unknown.
    """
    return MessageFact(
        id=message.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        guild_id=message.guild.id if message.guild is not None else None,
        content=message.content,
        created_at=Instant.from_py_datetime(message.created_at),
    )


def to_tracked_message(message: discord.Message) -> TrackedMessage:
    """Convert a guild Discord message to framework-neutral metadata."""
    if message.guild is None:
        msg = "Cannot track messages in DMs."
        raise ValueError(msg)
    return TrackedMessage(
        id=message.id,
        server_id=message.guild.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        content=message.content,
    )
