"""A cog for miscellaneous commands."""

from typing import TYPE_CHECKING

import discord.ext.commands as commands
from discord.ext.commands import Cog, Context

from squid.bot.utils.components import link_layout, no_mentions

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
        await ctx.send(
            view=link_layout(
                "Invite Redstone Squid",
                f"https://discordapp.com/oauth2/authorize?client_id={ctx.bot.user.id}&scope=bot&permissions=8",  # type: ignore
                label="Invite bot",
            ),
            allowed_mentions=no_mentions(),
        )

    # Note that the help text is replaced in the __init__ method
    # because the bot's name is not available at the time of class definition.
    @info_group.command(name="source")
    async def source_code(self, ctx: Context[BotT]):
        """Open the bot's source code."""
        await ctx.send(
            view=link_layout(
                "Source code",
                self.bot.source_code_url or "https://github.com/redstone-squid/Redstone-Squid",
                label="Open repository",
            ),
            allowed_mentions=no_mentions(),
        )

    @info_group.command(name="form")
    async def google_forms(self, ctx: Context[BotT]):
        """Open the legacy submission form. Prefer `/build submit` for new builds."""
        BUILD_SUBMISSION_FORM_LINK = "https://forms.gle/i9Nf6apGgPGTUohr9"
        await ctx.send(
            view=link_layout(
                "Submission form",
                BUILD_SUBMISSION_FORM_LINK,
                description="Submit a new record through the Google form.",
                label="Open form",
            ),
            allowed_mentions=no_mentions(),
        )

    @info_group.command(name="docs")
    async def docs(self, ctx: Context[BotT]):
        """Open the build rules and regulations."""
        await ctx.send(
            view=link_layout(
                "Regulations",
                "https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit",
                label="Read regulations",
            ),
            allowed_mentions=no_mentions(),
        )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Miscellaneous(bot))
