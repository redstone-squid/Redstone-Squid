"""Discord commands for querying and managing computed records."""

from typing import TYPE_CHECKING, Any

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, hybrid_group

import squid_ui_discord as sd
from squid.bot.i18n import resolve_locale, t
from squid.bot.ui import DISCORD_BLUE, PagedList, info_node
from squid.bot.utils.autocomplete import autocompletes, suggests
from squid.bot.utils.permissions import hide_unless, requires
from squid.bot.utils.visibility import personal
from squid.core.i18n import _
from squid.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.records.application import RecordLookupRequest
from squid.records.domain import BuildKind

if TYPE_CHECKING:
    import squid.bot.app

DIAGNOSTICS_PER_PAGE = 15
"""Findings per page. Each is one long line, so a page is a screenful rather than a wall."""


class RecordCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Query and maintain server-computed record categories."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.records = bot.services.records
        self.computation = bot.services.record_computation

    @hybrid_group(name="records")
    @requires(RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD, mode="any")
    @hide_unless(manage_guild=True)
    async def records_group(self, ctx: Context[BotT]) -> None:
        """Inspect and maintain computed record categories."""
        await ctx.send_help("records")

    @records_group.command(name="gaps")
    @requires(RECORD_ENTRY_INSPECT)
    @app_commands.describe(kind=app_commands.locale_str(_("Optionally limit gaps to one build kind.")))
    async def gaps(self, ctx: Context[BotT], kind: BuildKind | None = None) -> None:
        """List categories whose winner needs more factual evidence."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        gaps = await self.records.gaps(kind=kind)
        paginator = _diagnostic_list(
            ctx,
            t(locale, _("Record evidence gaps")),
            [
                f"`{gap.definition_id}` **{gap.title}** — builds {', '.join(map(str, gap.build_ids))}; "
                f"missing {', '.join(gap.fields)}"
                for gap in gaps
            ],
            empty=t(locale, _("No unresolved active record categories.")),
            locale=locale,
        )
        await paginator.send(ctx, visibility="personal")

    @records_group.command(name="title-issues")
    @requires(RECORD_ENTRY_INSPECT)
    @app_commands.describe(kind=app_commands.locale_str(_("Optionally limit title diagnostics to one build kind.")))
    async def title_gaps(self, ctx: Context[BotT], kind: BuildKind | None = None) -> None:
        """List canonical titles containing unknown or contradictory taxonomy."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        gaps = await self.records.title_gaps(kind=kind)
        paginator = _diagnostic_list(
            ctx,
            t(locale, _("Record title diagnostics")),
            [
                f"`{gap.definition_id}` **{gap.title}** — "
                + ", ".join(str(diagnostic.get("code", "unknown")) for diagnostic in gap.diagnostics)
                for gap in gaps
            ],
            empty=t(locale, _("No active record titles require taxonomy review.")),
            locale=locale,
        )
        await paginator.send(ctx, visibility="personal")

    @autocompletes(current_version_id="version_ids")
    @records_group.command(name="rebuild")
    @requires(RECORD_ENTRY_REBUILD)
    @app_commands.describe(
        current_version_id=app_commands.locale_str(
            _("Optional database ID to also compute the pinned current-version records.")
        ),
        kind=app_commands.locale_str(_("Optionally rebuild just doors or extenders.")),
    )
    async def rebuild(
        self,
        ctx: Context[BotT],
        current_version_id: int | None = None,
        kind: BuildKind | None = None,
    ) -> None:
        """Recompute records from confirmed build facts."""
        await ctx.defer(ephemeral=personal(ctx))
        invocation = await sd.Invocation.of(ctx)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        kinds = (kind,) if kind is not None else (BuildKind.DOOR, BuildKind.EXTENDER)
        summary = await self.computation.rebuild(current_version_id=current_version_id, kinds=kinds)
        await invocation.reply(
            info_node(
                t(locale, _("Records rebuilt")),
                t(
                    locale,
                    _("{definitions} definitions; {resolved} resolved; {unresolved} awaiting evidence."),
                    definitions=summary.definitions,
                    resolved=summary.resolved,
                    unresolved=summary.unresolved,
                ),
            ),
            visibility="personal",
        )

    @autocompletes(
        base_key="record_definitions",
        version_id="version_ids",
        restrictions=suggests("restriction_ids", multi=True),
    )
    @records_group.command(name="lookup")
    @requires(RECORD_ENTRY_INSPECT)
    @commands.cooldown(2, 60, commands.BucketType.user)
    @app_commands.describe(
        kind=app_commands.locale_str(_("The typed record family.")),
        base_key=app_commands.locale_str(_("Pick a record category, or paste a raw base key.")),
        restrictions=app_commands.locale_str(
            _("Pick restrictions from the list; large exact categories are saved for reuse.")
        ),
        version_id=app_commands.locale_str(_("Optional pinned version database ID.")),
    )
    async def lookup(
        self,
        ctx: Context[BotT],
        kind: BuildKind,
        base_key: str,
        restrictions: str = "",
        version_id: int | None = None,
    ) -> None:
        """Materialize and compute an arbitrary exact restriction category."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        invocation = await sd.Invocation.of(ctx)
        try:
            restriction_ids = frozenset(int(value.strip()) for value in restrictions.split(",") if value.strip())
        except ValueError as error:
            msg = t(locale, _("Pick restrictions from the suggestions rather than typing them."))
            raise commands.BadArgument(msg) from error
        # An autocomplete pick submits a definition id; a raw base key always contains "|".
        selected = base_key.strip()
        if selected.isdigit():
            if restriction_ids:
                msg = t(locale, _("Restrictions can only be combined with a hand-typed base key."))
                raise commands.BadArgument(msg)
            await ctx.defer()
            summary = await self.records.materialize_definition(int(selected), kind=kind, version_id=version_id)
        else:
            await ctx.defer()
            summary = await self.records.lookup_or_materialize(
                RecordLookupRequest(
                    kind=kind,
                    base_key=selected,
                    restriction_ids=restriction_ids,
                    version_id=version_id,
                )
            )
        await invocation.reply(
            info_node(
                t(locale, _("Record category materialized")),
                t(
                    locale,
                    _("Recomputed {definitions} definitions; {resolved} resolved."),
                    definitions=summary.definitions,
                    resolved=summary.resolved,
                ),
            ),
            # Staff maintenance, like `records rebuild` beside it: the two answered differently
            # for no reason anybody recorded, which is the pair audit C2 pointed at.
            visibility="personal",
        )


def _diagnostic_list(
    ctx: Context[Any],
    title: str,
    entries: list[str],
    *,
    empty: str,
    locale: str | None,
) -> PagedList:
    """A page of one-line findings.

    Both diagnostics used to print the first 30 findings and count the rest, which is the
    cap the reader hits exactly when the maintenance backlog is worth reading (audit C6).
    One line per finding, so the entries are joined by a newline rather than by the blank
    line multi-line entries want.
    """
    return PagedList(
        title,
        entries,
        empty=empty,
        locale=locale,
        page_size=DIAGNOSTICS_PER_PAGE,
        separator="\n",
        accent_colour=DISCORD_BLUE,
    )
