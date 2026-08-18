"""Discord commands for querying and managing computed records."""

from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, hybrid_group

from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.autocomplete import autocompletes, suggests
from squid.bot.utils.components import info_layout, no_mentions
from squid.bot.utils.permissions import hide_unless, requires
from squid.core.i18n import _
from squid.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.records.application import RecordLookupRequest
from squid.records.domain import BuildKind

if TYPE_CHECKING:
    import squid.bot.app


class RecordCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Query and maintain server-computed record categories."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.records = bot.services.records
        self.computation = bot.services.record_computation

    @hybrid_group(name="admin")
    @requires(RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD, mode="any")
    @hide_unless(manage_guild=True)
    async def admin_group(self, ctx: Context[BotT]) -> None:
        """Inspect and maintain internal bot data."""
        await ctx.send_help("admin")

    @admin_group.command(name="records-gaps")
    @requires(RECORD_ENTRY_INSPECT)
    @app_commands.describe(kind=app_commands.locale_str(_("Optionally limit gaps to one build kind.")))
    async def gaps(self, ctx: Context[BotT], kind: BuildKind | None = None) -> None:
        """List categories whose winner needs more factual evidence."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        gaps = await self.records.gaps(kind=kind)
        if gaps:
            description = "\n".join(
                f"`{gap.definition_id}` **{gap.record_class.value.upper()}** "
                f"{gap.category_key} — builds {', '.join(map(str, gap.build_ids))}; "
                f"missing {', '.join(gap.fields)}"
                for gap in gaps[:30]
            )
            if len(gaps) > 30:
                description += t(locale, _("\n…and {count} more."), count=len(gaps) - 30)
        else:
            description = t(locale, _("No unresolved active record categories."))
        await ctx.send(
            view=info_layout(t(locale, _("Record evidence gaps")), description), allowed_mentions=no_mentions()
        )

    @admin_group.command(name="records-title-issues")
    @requires(RECORD_ENTRY_INSPECT)
    @app_commands.describe(kind=app_commands.locale_str(_("Optionally limit title diagnostics to one build kind.")))
    async def title_gaps(self, ctx: Context[BotT], kind: BuildKind | None = None) -> None:
        """List canonical titles containing unknown or contradictory taxonomy."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        gaps = await self.records.title_gaps(kind=kind)
        if gaps:
            lines: list[str] = []
            for gap in gaps[:30]:
                codes = ", ".join(str(diagnostic.get("code", "unknown")) for diagnostic in gap.diagnostics)
                lines.append(f"`{gap.definition_id}` **{gap.title}** — {codes}")
            description = "\n".join(lines)
            if len(gaps) > 30:
                description += t(locale, _("\n…and {count} more."), count=len(gaps) - 30)
        else:
            description = t(locale, _("No active record titles require taxonomy review."))
        await ctx.send(
            view=info_layout(t(locale, _("Record title diagnostics")), description),
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    @autocompletes(current_version_id="version_ids")
    @admin_group.command(name="records-rebuild")
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
        await ctx.defer(ephemeral=ctx.interaction is not None)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        kinds = (kind,) if kind is not None else (BuildKind.DOOR, BuildKind.EXTENDER)
        summary = await self.computation.rebuild(current_version_id=current_version_id, kinds=kinds)
        await ctx.send(
            view=info_layout(
                t(locale, _("Records rebuilt")),
                t(
                    locale,
                    _("{definitions} definitions; {resolved} resolved; {unresolved} awaiting evidence."),
                    definitions=summary.definitions,
                    resolved=summary.resolved,
                    unresolved=summary.unresolved,
                ),
            ),
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    @autocompletes(
        base_key="record_definitions",
        version_id="version_ids",
        restrictions=suggests("restriction_ids", multi=True),
    )
    @admin_group.command(name="records-lookup")
    @requires(RECORD_ENTRY_INSPECT)
    @commands.cooldown(2, 60, commands.BucketType.user)
    @app_commands.describe(
        kind=app_commands.locale_str(_("The typed record family.")),
        base_key=app_commands.locale_str(_("Pick a record category, or paste a raw base key.")),
        restrictions=app_commands.locale_str(
            _("Comma-separated restriction IDs; large exact categories are saved for reuse.")
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
        try:
            restriction_ids = frozenset(int(value.strip()) for value in restrictions.split(",") if value.strip())
        except ValueError as error:
            msg = t(locale, _("Restrictions must be comma-separated numeric IDs."))
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
        await ctx.send(
            view=info_layout(
                t(locale, _("Record category materialized")),
                t(
                    locale,
                    _("Recomputed {definitions} definitions; {resolved} resolved."),
                    definitions=summary.definitions,
                    resolved=summary.resolved,
                ),
            ),
            allowed_mentions=no_mentions(),
        )
