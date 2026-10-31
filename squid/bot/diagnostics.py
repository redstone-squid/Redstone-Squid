"""Look up a stored error report from the reference a user quoted."""

from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands
from discord.ext.commands import Context

from squid.bot.diagnostics_view import ErrorReportView, report_attachment
from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import error_layout, info_layout, no_mentions
from squid.bot.utils.permissions import hide_unless, requires
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
        view = ErrorReportView(author_id=ctx.author.id, locale=locale, report=report, matches=matches)
        await self._deliver(ctx, view, locale, file=report_attachment(report))

    @error_group.command(name="recent")
    @requires(DIAGNOSTICS_ERROR_READ)
    async def recent_errors(self, ctx: Context[BotT], work_lost: bool = False) -> None:
        """List the most recent stored errors, newest first.

        Set work_lost to see only failures that permanently abandoned work, such as a
        dead-lettered job, rather than every exception something recovered from.
        """
        reports = await self.error_reports.recent(limit=RECENT_LIMIT, work_lost_only=work_lost)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await self._deliver(ctx, ErrorReportView(author_id=ctx.author.id, locale=locale, reports=reports), locale)

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

    async def _deliver(
        self,
        ctx: Context[BotT],
        layout: discord.ui.LayoutView,
        locale: str | None,
        *,
        file: discord.File | None = None,
    ) -> None:
        """Answer where only the caller can read it, whatever the transport invoked us.

        A report carries a traceback naming internal paths and the unredacted message every
        other surface withholds, so it is ephemeral on the slash side. `Context.send` silently
        drops `ephemeral` when there is no interaction, though, so the prefix form used to post
        that traceback — plus its log tail and attachment — into whichever channel it was typed
        in. There it goes to the author's direct messages instead, and the channel gets a line
        saying so.
        """
        payload: dict[str, Any] = {"view": layout, "allowed_mentions": no_mentions()}
        if file is not None:
            payload["file"] = file

        if ctx.interaction is not None or ctx.guild is None:
            message = await ctx.send(ephemeral=True, **payload)
        else:
            try:
                message = await ctx.author.send(**payload)
            except discord.Forbidden:
                await ctx.send(
                    view=error_layout(
                        t(locale, _("Could not send you the report")),
                        t(locale, _("Allow direct messages from this server, then run the command again.")),
                    ),
                    allowed_mentions=no_mentions(),
                )
                return
            await ctx.send(
                view=info_layout(
                    t(locale, _("Sent by direct message")),
                    t(locale, _("An error report names internal paths, so it is never posted in a channel.")),
                ),
                allowed_mentions=no_mentions(),
            )

        if isinstance(layout, ErrorReportView):
            layout.bind_message(message)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(Diagnostics(bot))
