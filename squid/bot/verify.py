"""A cog for verifying minecraft accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_command

from squid.bot.submission.ui.views import ConfirmationView
from squid.db.user import link_minecraft_account, unlink_minecraft_account

if TYPE_CHECKING:
    from squid.bot.main import RedstoneSquid


class VerifyCog[BotT: RedstoneSquid](Cog, name="verify"):
    def __init__(self, bot: BotT):
        self.bot = bot

    @hybrid_command()
    @app_commands.describe(code="The code you received by running /link in the game.")
    async def link(self, ctx: Context[BotT], code: str):
        """Link your minecraft account."""
        if await link_minecraft_account(ctx.author.id, code):
            await ctx.send("Your discord account has been linked with your minecraft account.")
        else:
            await ctx.send("Invalid code. Please generate a new code and try again.")

    @hybrid_command()
    async def unlink(self, ctx: Context[BotT]):
        """Unlink your minecraft account."""
        view = ConfirmationView()
        await ctx.send("Are you sure you want to unlink your minecraft account?", view=view)

        await view.wait()
        if view.value:
            if await unlink_minecraft_account(ctx.author.id):
                await ctx.send("Your discord account has been unlinked from your minecraft account.")
            else:
                await ctx.send("An error occurred while unlinking your account. Please try again later.")


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))
