"""Conversions between Discord messages and application message metadata."""

import discord
from whenever import Instant

from squid.messages.domain import MessageSnapshot


def to_message_snapshot(message: discord.Message) -> MessageSnapshot:
    """Copy Discord message metadata into a MessageSnapshot, using no guild ID for DMs."""
    return MessageSnapshot(
        id=message.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        guild_id=message.guild.id if message.guild is not None else None,
        content=message.content,
        created_at=Instant(message.created_at),
    )
