"""A cog for miscellaneous commands."""

from typing import TYPE_CHECKING

import discord.ext.commands as commands
from discord.ext.commands import Cog, Context

from squid.bot.i18n import resolve_locale, t
from squid.bot.ui import link_layout, reply_payload
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app


class Miscellaneous[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.source_code.help = f"Link to {self.bot.bot_name}'s source code."

    @commands.hybrid_group(name="info")
    async def info_group(self, ctx: Context[BotT]) -> None:
        """Open useful Redstone Squid links."""
        await ctx.send_help("info")

    @info_group.command(name="invite")
    async def invite_link(self, ctx: Context[BotT]):
        """Get a link to invite the bot to another server."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_payload(
            ctx,
            link_layout(
                t(locale, _("Invite Redstone Squid")),
                f"https://discordapp.com/oauth2/authorize?client_id={ctx.bot.user.id}&scope=bot&permissions=8",  # type: ignore
                label=t(locale, _("Invite bot")),
            ),
        )

    # Note that the help text is replaced in the __init__ method
    # because the bot's name is not available at the time of class definition.
    @info_group.command(name="source")
    async def source_code(self, ctx: Context[BotT]):
        """Open the bot's source code."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_payload(
            ctx,
            link_layout(
                t(locale, _("Source code")),
                self.bot.source_code_url or "https://github.com/redstone-squid/Redstone-Squid",
                label=t(locale, _("Open repository")),
            ),
        )

    @info_group.command(name="form")
    async def google_forms(self, ctx: Context[BotT]):
        """Open the legacy submission form. Prefer `/build submit` for new builds."""
        BUILD_SUBMISSION_FORM_LINK = "https://forms.gle/i9Nf6apGgPGTUohr9"
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_payload(
            ctx,
            link_layout(
                t(locale, _("Submission form")),
                BUILD_SUBMISSION_FORM_LINK,
                description=t(locale, _("Submit a new record through the Google form.")),
                label=t(locale, _("Open form")),
            ),
        )

    @info_group.command(name="docs")
    async def docs(self, ctx: Context[BotT]):
        """Open the build rules and regulations."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await reply_payload(
            ctx,
            link_layout(
                t(locale, _("Regulations")),
                "https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit",
                label=t(locale, _("Read regulations")),
            ),
        )


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Miscellaneous(bot))
