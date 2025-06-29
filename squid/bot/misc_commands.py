"""A cog for miscellaneous commands."""

from typing import TYPE_CHECKING

import discord
import discord.ext.commands as commands
from discord.ext.commands import Cog, Context

import squid.bot.utils as utils
from squid.utils import get_version_string

if TYPE_CHECKING:
    import squid.bot


class Miscellaneous[BotT: "squid.bot.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.source_code.help = f"Link to {self.bot.bot_name}'s source code."

    @commands.hybrid_command()
    async def invite_link(self, ctx: Context[BotT]):
        """Invite me to your other servers!"""
        await ctx.send(
            f"https://discordapp.com/oauth2/authorize?client_id={ctx.bot.user.id}&scope=bot&permissions=8"  # type: ignore
        )

    # Note that the help text is replaced in the __init__ method
    # because the bot's name is not available at the time of class definition.
    @commands.hybrid_command()
    async def source_code(self, ctx: Context[BotT]):
        """Link to the bot's source code."""
        await ctx.send(f"Source code can be found at: {self.bot.source_code_url}.")

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


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Miscellaneous(bot))
