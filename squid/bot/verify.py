"""A cog for verifying minecraft accounts."""

from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_command

from squid.bot.submission.ui.views import ConfirmationView

if TYPE_CHECKING:
    import squid.bot


class VerifyCog[BotT: squid.bot.RedstoneSquid](Cog, name="verify"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.user_service = bot.services.users

    @hybrid_command()
    @app_commands.describe(code="The code you received by running /link in the game.")
    async def link(self, ctx: Context[BotT], code: str):
        """Link your minecraft account."""
        await self.user_service.link_minecraft_account(ctx.author.id, code)
        await ctx.send("Your discord account has been linked with your minecraft account.")

    @hybrid_command()
    async def unlink(self, ctx: Context[BotT]):
        """Unlink your minecraft account."""
        view = ConfirmationView()
        await ctx.send("Are you sure you want to unlink your minecraft account?", view=view)

        await view.wait()
        if view.value:
            if await self.user_service.unlink_minecraft_account(ctx.author.id):
                await ctx.send("Your discord account has been unlinked from your minecraft account.")
            else:
                await ctx.send(
                    "You don't have a minecraft account linked to your discord account, or the unlinking failed."
                )


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))
