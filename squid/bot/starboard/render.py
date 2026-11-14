"""Components V2 starboard rendering."""

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.i18n import localization_for
from squid.bot.ui import render_payload
from squid.core.i18n import tr
from squid.starboard.application import EntryState
from squid_i18n import Text, discord_markup, emoji_text, md, md_tr, mention


def starboard_layout(
    state: EntryState, message: discord.Message, *, locale: str | None = None
) -> sd.message_payload.MessagePayload:
    """Render one source message as a semantic Components V2 card."""
    config = state.config
    entry = state.entry
    author_name = getattr(message.author, "display_name", message.author.name)
    avatar_url = message.author.display_avatar.url
    heading = md(t"**{author_name}**")
    if config.ping_author:
        heading = md(t"{mention(message.author.id)} · **{author_name}**")
    # The source message is already Discord markup, so it is copied as written.
    content = discord_markup(message.content) or tr(t"bot-starboard-render-no-text-content = -# (no text content)")
    children: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
        sd.v2.section(
            sd.v2.text(t"{heading}\n{content}"),
            accessory=sd.v2.thumbnail(avatar_url),
        )
    ]
    if config.replied_to and message.reference is not None and message.reference.message_id is not None:
        message_id = message.reference.message_id
        children.append(
            sd.v2.text(
                md_tr(
                    t"bot-starboard-render-replying-to-message = -# Replying to message `{message_id!s}`",
                )
            )
        )
    if config.attachments_list:
        media = tuple(attachment.url for attachment in message.attachments[:10])
        if media:
            children.append(sd.v2.gallery(*media))
    if config.jump_to_message:
        children.append(
            sd.v2.row(
                sd.v2.link_button(tr(t"bot-starboard-render-original-message = Original message"), message.jump_url),
            )
        )
    footer: list[sl.TextLike] = [Text(t"{emoji_text(config.display_emoji)} {entry.score:g}")]
    if entry.raw_count != entry.score:
        count = entry.raw_count
        footer.append(md_tr(t'bot-starboard-render-reactions = {{ " " }}({count} reactions)'))
    footer.append(Text(t" · {mention(message.channel.id, kind='channel')}"))
    children.append(sd.v2.footer(Text.join("", footer)))
    return render_payload(
        [sd.v2.panel(*(children), accent=config.colour)],
        localization=localization_for(locale),
    )
