"""Everything related to querying the database for information."""

import io
import json
import logging
from dataclasses import asdict
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, when_mentioned
from discord.utils import escape_markdown

import squid_layouts as sl
from squid.bot.i18n import resolve_locale, t
from squid.bot.operations import managed_result
from squid.bot.submission.build_info import BuildInfoComponent
from squid.bot.submission.consent_banner import BuildLogConsentStickyMessage
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.schematics import BuildSchematicCommands
from squid.bot.submission.search_view import SearchResultsView
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.ui import (
    PagedList,
    create_mount,
    destination,
    error_layout,
    error_node,
    info_node,
    reply_presentation,
    text_layout,
)
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import hide_unless, requires
from squid.bot.utils.visibility import personal
from squid.builds.domain import Build
from squid.builds.errors import AliasAlreadyAddedError
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_DEBUG,
    BUILD_SUBMISSION_REJECT,
    RESTRICTION_ALIAS_CREATE,
)
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort
from squid_layouts.runtime.component import RenderResult

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)


class SearchModeChoice(StrEnum):
    """User-facing names for search ranking modes."""

    keyword = "keyword"
    smart = "smart"


class SearchTarget(StrEnum):
    """What a search looks through.

    `patterns` and `restrictions` are the metadata scope plus a filter. Naming them
    here is what retired `/patterns search` and `/restrictions search`: nobody
    discovers `kind:pattern`, but everybody opens this option anyway.
    """

    records = "records"
    builds = "builds"
    patterns = "patterns"
    restrictions = "restrictions"
    everything = "everything"


_SEARCH_TARGETS: dict[SearchTarget, tuple[SearchScope, str | None]] = {
    SearchTarget.records: (SearchScope.RECORDS, None),
    SearchTarget.builds: (SearchScope.BUILDS, None),
    SearchTarget.patterns: (SearchScope.METADATA, "kind:pattern"),
    SearchTarget.restrictions: (SearchScope.METADATA, "kind:restriction"),
    SearchTarget.everything: (SearchScope.ALL, None),
}


def _targeted(target: SearchTarget, query: str) -> tuple[SearchScope, str]:
    """Resolve a target to a domain scope and the query expressing it.

    The user's text is parenthesised because AND binds tighter than OR:
    `kind:pattern a OR b` would parse as `(kind:pattern AND a) OR b`.
    """
    scope, narrowing = _SEARCH_TARGETS[target]
    if narrowing is None:
        return scope, query
    if not query.strip():
        return scope, narrowing
    return scope, f"{narrowing} ({query})"


def _pending_entry(build: Build, locale: str | None) -> str:
    """One line of the review queue: what it is, who made it, who sent it.

    The submitter is a mention rather than the snowflake the old list printed (audit C5).
    It can be absent: `submitter_discord_id` is derived from the account, and an account with
    no Discord identity linked has none.
    """
    creators = ", ".join(sorted(build.creators_ign))
    submitter = (
        f"<@{build.submitter_discord_id}>"
        if build.submitter_discord_id is not None
        else t(locale, _("someone unlinked"))
    )
    return t(
        locale,
        _("**#{id}** {title}\n-# by {creators} · submitted by {submitter}"),
        id=build.id,
        title=escape_markdown(build.title),
        creators=escape_markdown(creators) if creators else t(locale, _("unknown")),
        submitter=submitter,
    )


def _debug_dump(build: Build) -> str:
    """Serialize a build's internal state as readable JSON.

    This used to be `str(build.__dict__)` pasted into a message body, which Discord truncates
    at exactly the builds worth debugging and which renders enums as their repr. A file
    survives the length limit and opens in something that can fold it.

    `embedding` is dropped: a few thousand floats tell a reader nothing and would dominate the
    file. Its length is kept, because "is this build embedded at all" is a real question.
    """
    state: dict[str, Any] = {key: value for key, value in asdict(build).items() if key != "embedding"}
    state["category"] = build.category
    state["embedding_dimensions"] = len(build.embedding) if build.embedding is not None else None
    return json.dumps(_jsonable(state), indent=2, sort_keys=True, default=str)


def _jsonable(value: Any) -> Any:
    """Render enums by name, since an IntEnum otherwise serializes as the integer."""
    match value:
        case Enum():
            return value.name
        case dict():
            return {str(key): _jsonable(item) for key, item in value.items()}
        case list() | tuple():
            return [_jsonable(item) for item in value]
        case _:
            return value


class SearchCog[
    BotT: "squid.bot.app.RedstoneSquid",
](
    BuildEditCommands[BotT],
    BuildSubmitCommands[BotT],
    BuildSchematicCommands[BotT],
):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.queries = bot.services.build_queries
        self.search = bot.services.search
        self.builds = bot.services.builds
        self.inference = bot.services.build_inference
        self.messages = bot.services.messages
        self.restrictions = bot.services.restrictions
        self.consent_sticky = BuildLogConsentStickyMessage()
        self.register_edit_context_menu()
        self.register_recalc_context_menu()

    @override
    async def cog_unload(self) -> None:
        # The tree is the bot's, not the cog's, so a reload leaves both menus registered and
        # the second `add_command` raises rather than replacing them.
        for menu in (self.edit_ctx_menu, self.recalc_ctx_menu):
            self.bot.tree.remove_command(menu.name, type=menu.type)

    @autocompletes(sort="search_sorts", query="search_query")
    @commands.hybrid_command("search")
    @app_commands.describe(
        query=app_commands.locale_str(_("Search text and filters, e.g. `width:5`.")),
        scope=app_commands.locale_str(_("What to look through: records, builds, patterns, restrictions, or all.")),
        sort=app_commands.locale_str(_("Order results by a field, ascending or descending.")),
        mode=app_commands.locale_str(_("Use keyword matching or smart meaning-based matching.")),
    )
    async def search_records(
        self,
        ctx: Context[BotT],
        scope: SearchTarget = SearchTarget.records,
        sort: str | None = None,
        mode: SearchModeChoice = SearchModeChoice.keyword,
        *,
        query: str,
    ) -> None:
        """Search records, builds, patterns, and restrictions using text and field filters."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        search_scope, targeted_query = _targeted(scope, query)
        request = SearchRequest(
            targeted_query,
            scope=search_scope,
            mode=SearchMode.LEXICAL if mode is SearchModeChoice.keyword else SearchMode.SEMANTIC,
            # Parsed, not split: `search_sorts` suggests complete `field` / `-field` values.
            sort=SearchSort.parse(sort),
        )
        page = await self.search.search(request)
        queries = getattr(self, "queries", None)
        load_build = queries.get if queries is not None else None
        view = SearchResultsView(
            self.search,
            request,
            page,
            author_id=ctx.author.id,
            locale=locale,
            load_build=load_build,
            render_build=lambda build: self.bot.for_build(build).render_node(),
        )
        mount = view.mount()
        await mount.send(destination(ctx, locale=locale))

    @commands.hybrid_group(name="restrictions")
    @requires(RESTRICTION_ALIAS_CREATE)
    @hide_unless(manage_guild=True)
    async def restrictions_group(self, ctx: Context[BotT]) -> None:
        """Maintain the restriction taxonomy."""
        await ctx.send_help("restrictions")

    @autocompletes(restriction="approved_restrictions")
    @restrictions_group.command(name="add-alias")
    @requires(RESTRICTION_ALIAS_CREATE)
    @app_commands.describe(
        restriction=app_commands.locale_str(_("The restriction to add another name for.")),
        alias=app_commands.locale_str(_("The additional name.")),
    )
    @managed_result
    async def add_restriction_alias(self, ctx: Context[BotT], restriction: str, alias: str) -> RenderResult:
        """Add another name for a restriction."""
        locale = await resolve_locale(ctx, self.bot.services.settings)

        try:
            await self.restrictions.add_alias(restriction, alias)
        except AliasAlreadyAddedError:
            return info_node(
                t(locale, _("Already added")),
                t(locale, _("Alias already on this restriction.")),
            )
        return info_node(t(locale, _("Success")), t(locale, _("Alias added.")))

    @BuildCommandGroup.build_hybrid_group.command(name="queue")  # type: ignore
    async def get_pending_submissions(self, ctx: Context[BotT]):
        """Shows an overview of all submitted builds pending review."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        pending = await self.queries.pending()
        paginator = PagedList(
            t(locale, _("Pending submissions")),
            [_pending_entry(build, locale) for build in pending],
            empty=t(locale, _("Nothing is waiting for review.")),
            locale=locale,
            page_size=None,
        )
        await paginator.send(ctx)

    @autocompletes(build_id="builds")
    @BuildCommandGroup.build_hybrid_group.command(name="view")  # type: ignore
    @app_commands.rename(build_id="id")
    @app_commands.describe(build_id=app_commands.locale_str(_("The ID of the build you want to see.")))
    async def view_build(self, ctx: Context[BotT], build_id: int):
        """Displays a submission."""
        if ctx.interaction:
            locale = await resolve_locale(ctx, self.bot.services.settings)
            interaction = ctx.interaction
            await interaction.response.defer()
            build = await self.queries.get(build_id)
            if build is None:
                await reply_presentation(
                    ctx,
                    error_layout(t(locale, _("Error")), t(locale, _("No build with that ID."))),
                    visibility="personal",
                )
                return

            node = await self.bot.for_build(build).render_node()

            async def refresh(current_id: int) -> tuple[Build, sl.LayoutNode] | None:
                latest = await self.queries.get(current_id)
                if latest is None:
                    return None
                return latest, await self.bot.for_build(latest).render_node()

            component = BuildInfoComponent(build, node, refresh=refresh, locale=locale)
            navigator = sl.discord.navigation.Navigator(component)
            mount = create_mount(
                navigator,
                access=sl.discord.Everyone(),
                locale=locale,
                timeout=300,
                reactor=self.bot.layout_reactor,
            )
            await mount.send(sl.discord.respond_to(interaction, ephemeral=False, wait=True))
            return

        await self._view_build_prefix(ctx, build_id)

    @managed_result
    async def _view_build_prefix(self, ctx: Context[BotT], build_id: int) -> RenderResult:
        """Render a prefix-command build view through a managed result mount."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        build = await self.queries.get(build_id)

        if build is None:
            return error_node(t(locale, _("Error")), t(locale, _("No build with that ID.")))

        return await self.bot.for_build(build).render_node()

    @autocompletes(build_id="builds_pending")
    @BuildCommandGroup.build_hybrid_group.command(name="approve")  # type: ignore
    @requires(BUILD_SUBMISSION_APPROVE)
    @app_commands.rename(build_id="id")
    @app_commands.describe(build_id=app_commands.locale_str(_("The ID of the build you want to confirm.")))
    @managed_result
    async def confirm_build(self, ctx: Context[BotT], build_id: int) -> RenderResult:
        """Mark a submission as confirmed and publish it."""
        locale = await resolve_locale(ctx, self.bot.services.settings)

        await self.builds.confirm(build_id)
        return info_node(t(locale, _("Success")), t(locale, _("Submission has been confirmed.")))

    @autocompletes(build_id="builds_pending")
    @BuildCommandGroup.build_hybrid_group.command(name="reject")  # type: ignore
    @requires(BUILD_SUBMISSION_REJECT)
    @app_commands.rename(build_id="id")
    @app_commands.describe(build_id=app_commands.locale_str(_("The ID of the build you want to deny.")))
    @managed_result
    async def deny_build(self, ctx: Context[BotT], build_id: int) -> RenderResult:
        """Mark a submission as denied."""
        locale = await resolve_locale(ctx, self.bot.services.settings)

        await self.builds.deny(build_id)

        # Denying removes the card rather than editing it, which the renderer
        # expresses by wanting no posts for a build in this state.
        await self.bot.refresh_posts("build", str(build_id))
        return info_node(t(locale, _("Success")), t(locale, _("Submission has been denied.")))

    @autocompletes(build_id="builds")
    @BuildCommandGroup.build_hybrid_group.command(name="debug")  # type: ignore
    @requires(BUILD_SUBMISSION_DEBUG)
    @app_commands.rename(build_id="id")
    @app_commands.describe(
        build_id=app_commands.locale_str(_("The ID of the build whose debug details you want to see."))
    )
    async def debug_build(self, ctx: Context[BotT], build_id: int):
        """Display internal details for a build."""
        await ctx.defer(ephemeral=personal(ctx))
        locale = await resolve_locale(ctx, self.bot.services.settings)
        build = await self.queries.get(build_id)
        if build is None:
            await reply_presentation(
                ctx,
                error_layout(t(locale, _("Error")), t(locale, _("No build with that ID."))),
                visibility="personal" if personal(ctx) else "public",
            )
            return

        # One message carrying the file, rather than a running message that would then have to
        # be edited into holding an attachment it was not sent with.
        await reply_presentation(
            ctx,
            text_layout(t(locale, _("Internal state for build #{id} is attached."), id=build_id)),
            visibility="personal" if personal(ctx) else "public",
            files=[discord.File(io.BytesIO(_debug_dump(build).encode()), filename=f"build-{build_id}-debug.json")],
        )

    @Cog.listener("on_command_error")
    async def mention_fallback_search(self, ctx: Context[BotT], exception: commands.CommandError, /) -> None:  # type: ignore[override]
        """Fallback search when the bot is mentioned and no command is found."""

        # This listener is dispatched for every command error in the bot, so scoping it comes
        # first: anything else arrives with `ctx.command` set, and asserting before this check
        # raised inside `on_command_error` itself, swallowing the error being reported.
        if not isinstance(exception, commands.CommandNotFound):
            return

        assert ctx.command is None, "This listener should only handle non-commands."

        # Only handle messages that mention the bot
        content = ctx.message.content
        mention_variants = when_mentioned(ctx.bot, ctx.message)
        for mention in mention_variants:
            if content.startswith(mention):
                trimmed_content = content[len(mention) :].strip()
                break
        else:
            return  # Bot was not mentioned

        # This should never happen, but just in case
        if ctx.invoked_parents or ctx.invoked_subcommand:  # pragma: no cover
            logger.warning("A CommandNotFound is being raised despite a subcommand being invoked.")
            return  # don't interfere with other commands

        # For some reason, empty messages are not caught by CommandNotFound
        assert trimmed_content != "", "Trimmed content should not be empty."

        try:
            build_id = int(trimmed_content)
        except ValueError:
            await ctx.invoke(self.search_records, query=trimmed_content)
            return
        await ctx.invoke(self.view_build, build_id=build_id)


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SearchCog(bot))
