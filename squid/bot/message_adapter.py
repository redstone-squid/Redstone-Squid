"""Conversions between Discord messages and application message metadata."""

import discord
from whenever import Instant

from squid.messages.domain import MessageFact


def to_message_fact(message: discord.Message) -> MessageFact:
    """Convert a Discord message to the plain fact recorded for it.

    Accepts DMs: a fact is true regardless of where the message lives, and only the
    guild is unknown.
    """
    return MessageFact(
        id=message.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        guild_id=message.guild.id if message.guild is not None else None,
        content=message.content,
        created_at=Instant(message.created_at),
    )
