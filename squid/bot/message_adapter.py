"""Conversions between Discord messages and application message metadata."""

import discord

from squid.services.messages import TrackedMessage


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
