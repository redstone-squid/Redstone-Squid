"""Conversion from portable emoji metadata to discord.py values."""

import discord

from squid_ui.emoji import EmojiLike


def discord_emoji(emoji: EmojiLike | None) -> str | discord.PartialEmoji | None:
    """Return the discord.py value for a portable emoji.

    Accepts the authored `EmojiLike` rather than only the normalized `Emoji`: the dataclasses
    that hold one normalize in `__post_init__` but still declare the wide type, so callers
    reading those attributes legitimately hand us a bare shortcode string.
    """
    if emoji is None:
        return None
    if isinstance(emoji, str):
        return emoji
    if emoji.id is None:
        return emoji.name
    return discord.PartialEmoji(name=emoji.name, id=emoji.id, animated=emoji.animated)
