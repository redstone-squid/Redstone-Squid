"""Look up a stored error report from the reference a user quoted."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from squid.bot.diagnostics_view import ErrorReportScreen
from squid.bot.utils.permissions import allows, enforce, hide_unless
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ

if TYPE_CHECKING:
    import squid.bot.app

class Diagnostics[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Read stored error reports."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.error_reports = bot.services.error_reports

    @app_commands.command(name="errors")
    @app_commands.describe(reference="The error reference to open immediately")
    @hide_unless(manage_guild=True)
    async def errors(self, interaction: discord.Interaction[BotT], reference: str | None = None) -> None:
        """Browse private error reports, optionally opening one reference."""
        await enforce(interaction, DIAGNOSTICS_ERROR_READ)

        async def may_clear() -> bool:
            return await allows(interaction, DIAGNOSTICS_ERROR_CLEAR)

        await ErrorReportScreen(
            self.error_reports,
            reference=reference,
            can_clear=await may_clear(),
            authorize_clear=may_clear,
        ).show(interaction)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Diagnostics(bot))
