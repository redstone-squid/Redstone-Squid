"""Open a private preview of a build's original Discord message."""

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.message_preview import message_preview
from squid.bot.routes import AppRequest, AppRoutes, ScreenUnavailable
from squid.bot.ui import truncate_display_text
from squid_ui.ext.discord.target_types import ComponentsV2Target

router = AppRoutes()


@router.route("/builds/source/{channel_id}/{message_id}")
async def preview_build_source(request: AppRequest, channel_id: int, message_id: int) -> None:
    unavailable = "The original Discord message is unavailable or you do not have access to it."
    channel = await request.client.get_or_fetch_messageable_channel(channel_id)
    if not isinstance(channel, discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread):
        raise ScreenUnavailable(unavailable)

    try:
        member = await channel.guild.fetch_member(request.user.id)
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.read_message_history:
            raise ScreenUnavailable(unavailable)
        if isinstance(channel, discord.Thread) and channel.is_private() and not permissions.manage_threads:
            await channel.fetch_member(member.id)
        message = await channel.fetch_message(message_id)
    except (discord.Forbidden, discord.NotFound) as error:
        raise ScreenUnavailable(unavailable) from error

    excerpt = truncate_display_text(" ".join(message.content.split()), 300)
    card: sl.LayoutNode[ComponentsV2Target, sd.Request] = sl.section(
        sl.heading("Original message preview"),
        *message_preview(content=sl.plain(excerpt) if excerpt else None, jump_url=message.jump_url),
    )
    await request.respond(card, audience="personal")
