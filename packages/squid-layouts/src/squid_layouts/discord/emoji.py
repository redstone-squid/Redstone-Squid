"""Conversion from portable emoji metadata to discord.py values."""

import discord

from squid_layouts.emoji import Emoji


def discord_emoji(emoji: Emoji | None) -> str | discord.PartialEmoji | None:
    """Return the discord.py value for a normalized portable emoji."""
    if emoji is None:
        return None
    if emoji.id is None:
        return emoji.name
    return discord.PartialEmoji(name=emoji.name, id=emoji.id, animated=emoji.animated)
