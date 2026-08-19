"""Look up a stored error report from the reference a user quoted."""

from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from discord.ext.commands import Context

from squid.bot.diagnostics_view import SESSION_SECONDS, ErrorReportBrowser, report_attachment
from squid.bot.i18n import resolve_locale, t
from squid.bot.ui import create_mount
from squid.bot.utils.components import info_layout
from squid.bot.utils.permissions import hide_unless, requires
from squid.bot.utils.visibility import deliver_privately
from squid.core.i18n import _
from squid.permissions.domain.catalogue import DIAGNOSTICS_ERROR_CLEAR, DIAGNOSTICS_ERROR_READ

if TYPE_CHECKING:
    import squid.bot.app

RECENT_LIMIT = 10


class Diagnostics[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Read stored error reports."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.error_reports = bot.services.error_reports

    @commands.hybrid_group(name="error", fallback="show")
    @requires(DIAGNOSTICS_ERROR_READ)
    @hide_unless(manage_guild=True)
    async def error_group(self, ctx: Context[BotT], reference: str) -> None:
        """Show the stored error behind a reference someone reported."""
        report, matches = await self.error_reports.lookup(reference)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        browser = ErrorReportBrowser(locale=locale, report=report, matches=matches)
        await self._deliver_browser(ctx, browser, locale, file=report_attachment(report))

    @error_group.command(name="recent")
    @requires(DIAGNOSTICS_ERROR_READ)
    async def recent_errors(self, ctx: Context[BotT], work_lost: bool = False) -> None:
        """List the most recent stored errors, newest first.

        Set work_lost to see only failures that permanently abandoned work, such as a
        dead-lettered job, rather than every exception something recovered from.
        """
        reports = await self.error_reports.recent(limit=RECENT_LIMIT, work_lost_only=work_lost)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await self._deliver_browser(ctx, ErrorReportBrowser(reports, locale=locale), locale)

    @error_group.command(name="clear")
    @requires(DIAGNOSTICS_ERROR_CLEAR)
    async def clear_errors(self, ctx: Context[BotT]) -> None:
        """Delete every stored error report, expired or not."""
        deleted = await self.error_reports.clear_all()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await self._deliver(
            ctx,
            info_layout(
                t(locale, _("Errors cleared")),
                t(locale, _("Deleted {count} stored error reports."), count=deleted),
            ),
            locale,
        )

    async def _deliver_browser(
        self,
        ctx: Context[BotT],
        browser: ErrorReportBrowser,
        locale: str | None,
        *,
        file: discord.File | None = None,
    ) -> None:
        """Mount the browser and answer where only the caller can read it.

        A report carries a traceback naming internal paths and the unredacted message every
        other surface withholds, which is the payload class `deliver_privately` exists for:
        ephemeral on the slash side, direct messages on the prefix side, never the channel.
        """
        mount = create_mount(browser, chrome=browser.chrome(), timeout=SESSION_SECONDS, lock_to=ctx.author.id)
        view = mount.build_view()
        message = await deliver_privately(
            ctx,
            view,
            reason=t(locale, _("An error report names internal paths, so it is never posted in a channel.")),
            locale=locale,
            file=file,
        )
        if message is not None:
            mount.bind(message, view)

    async def _deliver(
        self,
        ctx: Context[BotT],
        layout: discord.ui.LayoutView,
        locale: str | None,
    ) -> None:
        """Answer a plain layout where only the caller can read it (see `_deliver_browser`)."""
        await deliver_privately(
            ctx,
            layout,
            reason=t(locale, _("An error report names internal paths, so it is never posted in a channel.")),
            locale=locale,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Diagnostics(bot))
