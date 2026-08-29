"""Components V2 starboard rendering."""

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.i18n import localization_for, t
from squid.bot.ui import render_payload
from squid.core.i18n import _
from squid.starboard.application import EntryState


def starboard_layout(
    state: EntryState, message: discord.Message, *, locale: str | None = None
) -> sd.message_payload.MessagePayload:
    """Render one source message as a semantic Components V2 card."""
    config = state.config
    entry = state.entry
    author_name = getattr(message.author, "display_name", message.author.name)
    avatar_url = message.author.display_avatar.url
    heading = f"**{author_name}**"
    if config.ping_author:
        heading = f"{message.author.mention} · {heading}"
    children: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
        sd.v2.section(
            f"{heading}\n{message.content or t(locale, _('-# (no text content)'))}",
            accessory=sd.v2.thumbnail(avatar_url),
        )
    ]
    if config.replied_to and message.reference is not None and message.reference.message_id is not None:
        children.append(
            sd.v2.text(
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
            children.append(sd.v2.gallery(*media))
    if config.jump_to_message:
        children.append(sd.v2.row(sd.v2.link_button(t(locale, _("Original message")), message.jump_url)))
    score = f"{config.display_emoji} {entry.score:g}"
    if entry.raw_count != entry.score:
        score += t(locale, _(" ({count} reactions)"), count=entry.raw_count)
    children.append(sd.v2.footer(f"{score} · <#{message.channel.id}>"))
    return render_payload(
        [sd.v2.panel(*children, accent=config.colour)],
        localization=localization_for(locale),
    )
