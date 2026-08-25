"""Components V2 starboard rendering."""

import discord

import squid_discord as sd
import squid_layouts as sl
from squid.bot.i18n import t
from squid.bot.ui import render_presentation
from squid.core.i18n import _
from squid.starboard.application import EntryState


def starboard_layout(
    state: EntryState, message: discord.Message, *, locale: str | None = None
) -> sd.presentation.DiscordPresentation:
    """Render one source message as a semantic Components V2 card."""
    config = state.config
    entry = state.entry
    author_name = getattr(message.author, "display_name", message.author.name)
    avatar_url = message.author.display_avatar.url
    heading = f"**{author_name}**"
    if config.ping_author:
        heading = f"{message.author.mention} · {heading}"
    children: list[sl.primitives.Node] = [
        sl.primitives.Section(
            (sl.primitives.Text(f"{heading}\n{message.content or t(locale, _('-# (no text content)'))}"),),
            sl.primitives.Thumbnail(avatar_url),
        )
    ]
    if config.replied_to and message.reference is not None and message.reference.message_id is not None:
        children.append(
            sl.primitives.Text(
                t(
                    locale,
                    _("-# Replying to message \x60{message_id}\x60"),
                    message_id=message.reference.message_id,
                )
            )
        )
    if config.attachments_list:
        media = tuple(attachment.url for attachment in message.attachments[:10])
        if media:
            children.append(sl.primitives.Gallery(media))
    if config.jump_to_message:
        children.append(
            sl.primitives.Row((sl.primitives.LinkButton(t(locale, _("Original message")), message.jump_url),))
        )
    score = f"{config.display_emoji} {entry.score:g}"
    if entry.raw_count != entry.score:
        score += t(locale, _(" ({count} reactions)"), count=entry.raw_count)
    children.append(sl.primitives.Footer(f"{score} · <#{message.channel.id}>"))
    return render_presentation([sl.primitives.Panel(tuple(children), accent=config.colour)], locale=locale)
