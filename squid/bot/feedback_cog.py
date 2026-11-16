"""Publish feedback and notify the bot owner where they can read it."""

import logging
from typing import TYPE_CHECKING, Self

import discord
from discord import app_commands

import squid_ui.ext.discord as sd
from squid.bot._types import GuildMessageable
from squid.bot.ui import render_payload, text_node
from squid_i18n import Text, mention, tr

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


async def owner_can_view(channel: discord.abc.Messageable | None, owner_id: int) -> bool:
    """Check guild access, including membership for private threads; uncertainty uses DM."""
    if not isinstance(channel, GuildMessageable):
        return False
    try:
        member = channel.guild.get_member(owner_id) or await channel.guild.fetch_member(owner_id)
        permissions = channel.permissions_for(member)
        if not permissions.view_channel:
            return False
        if isinstance(channel, discord.Thread) and channel.is_private() and not permissions.manage_threads:
            await channel.fetch_member(owner_id)
    except discord.HTTPException, discord.ClientException:
        return False
    return True


class FeedbackCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Accept public bug reports and suggestions without requiring an account."""

    @sd.command(
        name=tr(t"command-bug-name = bug"),
        description=tr(t"command-bug-help = Report a bug with the bot. Your message will be public."),
        defer="public",
        options={
            "message": sd.Option(
                name=tr(t"command-bug-message-name = message"),
                description=tr(t"command-bug-message-help = Describe the bug"),
            )
        },
    )
    async def bug(self, request: sd.Request[Self, BotT], message: app_commands.Range[str, 1, 3000]) -> None:
        await self._report(request, "Bug report", message)

    @sd.command(
        name=tr(t"command-suggestion-name = suggestion"),
        description=tr(t"command-suggestion-help = Suggest improvements to the bot. Your message will be public."),
        defer="public",
        options={
            "message": sd.Option(
                name=tr(t"command-suggestion-message-name = message"),
                description=tr(t"command-suggestion-message-help = Describe your suggestion"),
            )
        },
    )
    async def suggestion(self, request: sd.Request[Self, BotT], message: app_commands.Range[str, 1, 3000]) -> None:
        await self._report(request, "Suggestion", message)

    async def _report(self, request: sd.Request[Self, BotT], kind: str, message: str) -> None:
        owner_id = self.bot.owner_id
        if owner_id is None:
            await request.respond("Feedback is unavailable: no bot owner is configured.", audience="public")
            return

        report = Text(f"{kind} from ") + mention(request.user.id) + f" ({request.user.id})\n{message}"
        if await owner_can_view(request.channel, owner_id):
            await request.respond(
                text_node(mention(owner_id) + "\n" + report),
                audience="public",
                allowed_mentions=discord.AllowedMentions(
                    users=[discord.Object(owner_id)], roles=False, everyone=False, replied_user=False
                ),
            )
            return

        await request.respond(text_node(report), audience="public")
        if request.guild is not None and isinstance(request.channel, GuildMessageable):
            context = (
                f"Server: {request.guild.name}\nhttps://discord.com/channels/{request.guild.id}/{request.channel.id}"
            )
        else:
            context = "Submitted in a direct message."
        try:
            owner = self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id)
            await sd.send_to(owner, allowed_mentions=discord.AllowedMentions.none())(
                render_payload([text_node(report + f"\n\n{context}")])
            )
        except discord.HTTPException:
            logger.warning("Could not DM feedback to bot owner %s", owner_id, exc_info=True)
            await request.respond(
                "Your feedback was posted, but I couldn't DM the bot owner. Please contact them directly.",
                audience="public",
            )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(FeedbackCog(bot))
