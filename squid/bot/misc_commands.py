"""A cog for miscellaneous commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import discord.ext.commands as commands
from discord.ext.commands import Cog, Context

import squid.bot.utils as utils
from squid.config import BOT_NAME, FORM_LINK, SOURCE_CODE_URL
from squid.db import get_version_string

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


class Miscellaneous[BotT: RedstoneSquid](Cog):
    def __init__(self, bot: BotT):
        self.bot: RedstoneSquid = bot

    @commands.hybrid_command()
    async def invite_link(self, ctx: Context[BotT]):
        """Invite me to your other servers!"""
        await ctx.send(
            f"https://discordapp.com/oauth2/authorize?client_id={ctx.bot.user.id}&scope=bot&permissions=8"  # type: ignore
        )

    # Docstring can't be an f-string, so we use the help parameter instead.
    @commands.hybrid_command(help=f"Link to {BOT_NAME}'s source code.")
    async def source_code(self, ctx: Context[BotT]):
        """Send a link to the source code."""
        await ctx.send(f"Source code can be found at: {SOURCE_CODE_URL}.")

    @commands.hybrid_command()
    async def google_forms(self, ctx: Context[BotT]):
        """Links you to our record submission form. You want to use /submit instead."""
        BUILD_SUBMISSION_FORM_LINK = "https://forms.gle/i9Nf6apGgPGTUohr9"
        em = discord.Embed(
            title="Submission form.",
            description=f"You can submit new records with ease via our google form: {BUILD_SUBMISSION_FORM_LINK}",
            colour=utils.discord_green,
        )
        await ctx.send(embed=em)

    @commands.hybrid_command()
    async def docs(self, ctx: Context[BotT]):
        """Links you to our regulations."""
        await ctx.send("https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit")

    @commands.hybrid_command(name="versions")
    async def versions(self, ctx: Context[BotT]):
        """Shows a list of versions the bot recognizes."""
        versions = await self.bot.db.get_or_fetch_versions_list(edition="Java")
        versions_human_readable = [get_version_string(version) for version in versions[:20]]  # TODO: pagination
        await ctx.send(", ".join(versions_human_readable))


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Miscellaneous(bot))
