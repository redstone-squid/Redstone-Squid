"""Components V2 starboard rendering."""

from typing import Any

import discord

from squid.bot.i18n import t
from squid.bot.utils.components import MAX_DISPLAY_CHARACTERS, StaticLayout, truncate_display_text
from squid.core.i18n import _
from squid.starboard.application import EntryState


def starboard_layout(state: EntryState, message: discord.Message, *, locale: str | None = None) -> StaticLayout:
    """Render one source message as a Components V2 card."""
    config = state.config
    entry = state.entry
    author_name = getattr(message.author, "display_name", message.author.name)
    avatar_url = str(message.author.display_avatar.url)
    heading = f"**{author_name}**"
    if config.ping_author:
        heading = f"{message.author.mention} · {heading}"
    content = truncate_display_text(
        message.content or t(locale, _("-# (no text content)")), MAX_DISPLAY_CHARACTERS - 300
    )
    children: list[discord.ui.Item[Any]] = [
        discord.ui.Section(f"{heading}\n{content}", accessory=discord.ui.Thumbnail(avatar_url))
    ]
    if config.replied_to and message.reference is not None and message.reference.message_id is not None:
        children.append(
            discord.ui.TextDisplay(
                t(
                    locale,
                    _("-# Replying to message `{message_id}`"),
                    message_id=message.reference.message_id,
                )
            )
        )
    if config.attachments_list:
        media = [attachment.url for attachment in message.attachments[:10]]
        if media:
            children.append(discord.ui.MediaGallery(*(discord.MediaGalleryItem(url) for url in media)))
    if config.jump_to_message:
        children.append(
            discord.ui.ActionRow(discord.ui.Button(label=t(locale, _("Original message")), url=message.jump_url))
        )
    score = f"{config.display_emoji} {entry.score:g}"
    if entry.raw_count != entry.score:
        score += t(locale, _(" ({count} reactions)"), count=entry.raw_count)
    children.append(discord.ui.TextDisplay(f"-# {score} · <#{message.channel.id}>"))
    return StaticLayout(discord.ui.Container(*children, accent_colour=config.colour))
