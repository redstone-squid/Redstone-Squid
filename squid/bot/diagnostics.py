"""Look up a stored error report from the reference a user quoted."""

import io
from typing import TYPE_CHECKING

from discord import File
from discord.ext import commands
from discord.ext.commands import Context

from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import CardField, card_layout, info_layout, no_mentions
from squid.bot.utils.permissions import requires
from squid.core.i18n import _
from squid.diagnostics.domain import ErrorReport
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_READ

if TYPE_CHECKING:
    import squid.bot.app

TRACEBACK_PREVIEW_CHARS = 1200
"""How much traceback goes in the card before the rest moves to an attachment.

A card has a 4000-character display budget shared with every other field, and a real traceback
routinely exceeds it on its own. The tail is previewed because that is where the failure is; the
whole thing is attached so nothing is actually lost.
"""

RECENT_LIMIT = 10


class Diagnostics[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Read stored error reports."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.error_reports = bot.services.error_reports

    @commands.hybrid_group(name="error", fallback="show")
    @requires(DIAGNOSTICS_ERROR_READ)
    async def error_group(self, ctx: Context[BotT], reference: str) -> None:
        """Show the stored error behind a reference someone reported."""
        report, matches = await self.error_reports.lookup(reference)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=card_layout(
                t(locale, _("Error {reference}"), reference=report.reference),
                _preview(report, locale),
                fields=_summary_fields(report, matches, locale),
            ),
            file=_attachment(report),
            # Always ephemeral: a traceback names internal paths and carries the unredacted
            # message every other surface deliberately withholds.
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )

    @error_group.command(name="recent")
    @requires(DIAGNOSTICS_ERROR_READ)
    async def recent_errors(self, ctx: Context[BotT], work_lost: bool = False) -> None:
        """List the most recent stored errors, newest first.

        Set work_lost to see only failures that permanently abandoned work, such as a
        dead-lettered job, rather than every exception something recovered from.
        """
        reports = await self.error_reports.recent(limit=RECENT_LIMIT, work_lost_only=work_lost)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        body = "\n".join(
            f"{':warning: ' if report.work_lost else ''}`{report.reference}` — "
            f"{report.exception_type} in {report.origin or report.surface}"
            for report in reports
        )
        await ctx.send(
            view=info_layout(
                t(locale, _("Recent errors")),
                body or t(locale, _("Nothing has failed within the retention window.")),
            ),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )


def _summary_fields(report: ErrorReport, matches: int, locale: str | None) -> list[CardField]:
    fields = [
        CardField(t(locale, _("When")), f"<t:{report.occurred_at.timestamp()}:f>"),
        CardField(t(locale, _("Where")), f"{report.surface} — {report.origin or '—'}"),
        CardField(t(locale, _("Exception")), report.exception_type),
        CardField(t(locale, _("Full ID")), f"`{report.correlation_id}`"),
    ]
    if report.work_lost:
        fields.append(
            CardField(
                t(locale, _("Work lost")),
                t(locale, _("This job was abandoned; nothing will retry it.")),
            )
        )
    if matches > 1:
        # The reference is a 48-bit prefix, not a key. Silently showing the newest of several
        # would have a moderator confidently reading the wrong incident.
        fields.append(
            CardField(
                t(locale, _("Ambiguous")),
                t(locale, _("{count} reports share this reference; this is the newest."), count=matches),
            )
        )
    return fields


def _preview(report: ErrorReport, locale: str | None) -> str:
    del locale
    tail = report.traceback[-TRACEBACK_PREVIEW_CHARS:]
    marker = "…\n" if len(report.traceback) > TRACEBACK_PREVIEW_CHARS else ""
    return f"```\n{marker}{tail}\n```"


def _attachment(report: ErrorReport) -> File:
    """Bundle the traceback and the log tail, which never fit in a card together."""
    lines = [
        f"reference: {report.reference}",
        f"correlation_id: {report.correlation_id}",
        f"occurred_at: {report.occurred_at}",
        f"surface: {report.surface}",
        f"origin: {report.origin or '-'}",
        f"exception: {report.exception_type}",
        f"code: {report.error_code.value if report.error_code else '-'}",
        f"work_lost: {report.work_lost}",
        f"message: {report.message}",
        f"context: {dict(report.context)}",
        "",
        "traceback:",
        report.traceback,
        "",
        "log tail:",
        *report.log_tail,
    ]
    return File(io.BytesIO("\n".join(lines).encode()), filename=f"error-{report.reference}.txt")


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Diagnostics(bot))
