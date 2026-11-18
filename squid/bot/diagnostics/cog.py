"""Look up a stored error report from the reference a user quoted."""

from math import isfinite
from typing import TYPE_CHECKING, Self

import squid_ui.ext.discord as sd
from squid.bot.diagnostics.screen import ErrorReportScreen
from squid.bot.ui import CardField, card_node
from squid.bot.utils.permissions import allows, enforce, hide_unless
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ
from squid_i18n import Text, tr

if TYPE_CHECKING:
    import squid.bot.app


class Diagnostics[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Read stored error reports."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.error_reports = bot.services.error_reports

    @sd.command(
        name=tr(t"command-connection-name = connection"),
        description=tr(t"command-connection-help = Inspect the bot's gateway connection and recent reconnects."),
        defer="private",
    )
    @hide_unless(manage_guild=True)
    async def connection(self, request: sd.Request[Self, BotT]) -> sd.CommandResult:
        await enforce(request, DIAGNOSTICS_ERROR_READ)
        history = self.bot.gateway_history
        counts = history.counts
        identifies, resumes, disconnects = counts["identify"], counts["resume"], counts["disconnect"]
        ready = self.bot.is_ready()
        latency = round(self.bot.latency * 1000) if isfinite(self.bot.latency) else None
        events = Text.join(
            "\n",
            (
                Text(t"{discord_timestamp} · {entry.kind}")
                for entry in reversed(tuple(history.events)[-10:])
                for discord_timestamp in (f"<t:{int(entry.occurred_at.timestamp())}:T>",)
            ),
        )
        return card_node(
            tr(t"connection-heading = Discord connection"),
            tr(
                t"connection-counts = Since startup: {identifies} identifies, {resumes} resumes, {disconnects} disconnects."
            ),
            fields=(
                CardField(tr(t"connection-ready = Ready"), str(ready)),
                CardField(tr(t"connection-latency = Heartbeat latency (ms)"), str(latency)),
                CardField(tr(t"connection-events = Recent events"), events or "—"),
            ),
        )

    @sd.command(
        name=tr(t"command-errors-name = errors"),
        description=tr(t"command-errors-help = Browse private error reports, optionally opening one reference."),
        options={
            "reference": sd.Option(
                name=tr(t"command-errors-reference-name = reference"),
                description=tr(t"command-errors-reference-help = The error reference, full ID, or ID prefix to open"),
            )
        },
    )
    @hide_unless(manage_guild=True)
    async def errors(self, request: sd.Request[Self, BotT], reference: str | None = None) -> ErrorReportScreen:
        """Browse private error reports, optionally opening one reference."""
        await enforce(request, DIAGNOSTICS_ERROR_READ)

        async def may_clear() -> bool:
            return await allows(request, DIAGNOSTICS_ERROR_CLEAR)

        return ErrorReportScreen(
            self.error_reports,
            reference=reference,
            can_clear=await may_clear(),
            authorize_clear=may_clear,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Diagnostics(bot))
