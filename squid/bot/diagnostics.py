"""Look up a stored error report from the reference a user quoted."""

from typing import TYPE_CHECKING, Self

from discord import app_commands

import squid_ui_discord as sd
from squid.bot.diagnostics_view import ErrorReportScreen
from squid.bot.utils.permissions import allows, enforce, hide_unless
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ

if TYPE_CHECKING:
    import squid.bot.app


class Diagnostics[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    """Read stored error reports."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.error_reports = bot.services.error_reports

    @sd.command(name="errors")
    @app_commands.describe(reference="The error reference to open immediately")
    @hide_unless(manage_guild=True)
    async def errors(self, request: sd.Request[Self], reference: str | None = None) -> ErrorReportScreen:
        """Browse private error reports, optionally opening one reference."""
        await enforce(request, DIAGNOSTICS_ERROR_READ)

        async def may_clear() -> bool:
            return await allows(request, DIAGNOSTICS_ERROR_CLEAR)

        return ErrorReportScreen(
            self.error_reports,
            bus=self.bot.topic_bus,
            reference=reference,
            can_clear=await may_clear(),
            authorize_clear=may_clear,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Diagnostics(bot))
