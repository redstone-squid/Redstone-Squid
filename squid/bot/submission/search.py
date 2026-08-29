"""Search and canonical build browsing entry points."""

import json
import logging
from dataclasses import asdict
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, when_mentioned

from squid.bot.submission.build_browse import BuildBrowseScreen, BuildCapabilities
from squid.bot.submission.consent_banner import BuildLogConsentStickyMessage
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import allows, subject_for_interaction
from squid.builds.domain import Build
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SCHEMATIC_DETECT_LATTICE,
    BUILD_SCHEMATIC_MEASURE_TIMING,
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_DEBUG,
    BUILD_SUBMISSION_EDIT,
    BUILD_SUBMISSION_REJECT,
    BUILD_SUBMISSION_VIEW_PENDING,
)
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class SearchModeChoice(StrEnum):
    """User-facing names for search ranking modes."""

    keyword = "keyword"
    smart = "smart"


class SearchTarget(StrEnum):
    """What a search looks through."""

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
    """Resolve a target to a domain scope and the query expressing it."""
    scope, narrowing = _SEARCH_TARGETS[target]
    if narrowing is None:
        return scope, query
    if not query.strip():
        return scope, narrowing
    return scope, f"{narrowing} ({query})"


def _debug_dump(build: Build) -> str:
    """Serialize a build's internal state as readable JSON without its embedding."""
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


class SearchCog[BotT: "squid.bot.app.RedstoneSquid"](
    BuildEditCommands[BotT],
    BuildSubmitCommands[BotT],
):
    """Own app-only search, build browsing, and build submission."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.queries = bot.services.build_queries
        self.search = bot.services.search
        self.builds = bot.services.builds
        self.inference = bot.services.build_inference
        self.messages = bot.services.messages
        self.consent_sticky = BuildLogConsentStickyMessage()
        self.register_edit_context_menu()
        self.register_recalc_context_menu()

    @override
    async def cog_unload(self) -> None:
        for menu in (self.edit_ctx_menu, self.recalc_ctx_menu):
            self.bot.tree.remove_command(menu.name, type=menu.type)

    @autocompletes(sort="search_sorts", query="search_query")
    @app_commands.command(name="search", description="Search builds, records, and taxonomy metadata")
    @app_commands.describe(
        query=app_commands.locale_str("Search text and filters, e.g. `width:5`."),
        scope=app_commands.locale_str("What to look through: records, builds, patterns, restrictions, or all."),
        sort=app_commands.locale_str("Order results by a field, ascending or descending."),
        mode=app_commands.locale_str("Use keyword matching or smart meaning-based matching."),
    )
    async def search_records(
        self,
        interaction: discord.Interaction[BotT],
        scope: SearchTarget = SearchTarget.records,
        sort: str | None = None,
        mode: SearchModeChoice = SearchModeChoice.keyword,
        *,
        query: str,
    ) -> None:
        """Search records, builds, patterns, and restrictions using text and field filters."""
        await interaction.response.defer()
        await self._show_search(interaction, scope=scope, sort=sort, mode=mode, query=query)

    async def _show_search(
        self,
        source: Context[BotT] | discord.Interaction[BotT],
        *,
        scope: SearchTarget = SearchTarget.records,
        sort: str | None = None,
        mode: SearchModeChoice = SearchModeChoice.keyword,
        query: str,
    ) -> None:
        search_scope, targeted_query = _targeted(scope, query)
        request = SearchRequest(
            targeted_query,
            scope=search_scope,
            mode=SearchMode.LEXICAL if mode is SearchModeChoice.keyword else SearchMode.SEMANTIC,
            sort=SearchSort.parse(sort),
        )
        page = await self.search.search(request)
        await SearchScreen(
            self.search,
            request,
            page,
            load_build=self.queries.get,
            render_build=lambda build: self.bot.for_build(build).render_node(),
        ).show(source)

    @autocompletes(build_id="builds")
    @BuildCommandGroup.build_group.command(name="browse")
    @app_commands.rename(build_id="id")
    @app_commands.describe(build_id=app_commands.locale_str("Open this build directly."))
    async def browse_builds(self, interaction: discord.Interaction[BotT], build_id: int | None = None) -> None:
        """Browse searchable build details, review actions, and schematic tools."""
        subject = await subject_for_interaction(interaction)
        permissions = self.bot.services.permissions

        async def held(node: PermissionNode) -> bool:
            return await permissions.allows(subject, node)

        capabilities = BuildCapabilities(
            view_pending=await held(BUILD_SUBMISSION_VIEW_PENDING),
            approve=await held(BUILD_SUBMISSION_APPROVE),
            reject=await held(BUILD_SUBMISSION_REJECT),
            debug=await held(BUILD_SUBMISSION_DEBUG),
            edit=await held(BUILD_SUBMISSION_EDIT),
            measure_timing=await held(BUILD_SCHEMATIC_MEASURE_TIMING),
            detect_lattice=await held(BUILD_SCHEMATIC_DETECT_LATTICE),
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(interaction, node)

        async def refresh_posts(build_id: int) -> None:
            await self.bot.refresh_posts("build", str(build_id))

        await BuildBrowseScreen(
            self.queries,
            self.builds,
            self.bot.services.schematics,
            initial_id=build_id,
            render_build=lambda build: self.bot.for_build(build).render_node(),
            capabilities=capabilities,
            actor_account_id=subject.account_id,
            authorize=authorize,
            refresh_posts=refresh_posts,
        ).show(interaction)

    @Cog.listener("on_command_error")
    async def mention_fallback_search(self, ctx: Context[BotT], exception: commands.CommandError, /) -> None:  # type: ignore[override]
        """Search when the bot is mentioned without a command."""
        if not isinstance(exception, commands.CommandNotFound):
            return
        assert ctx.command is None, "This listener should only handle non-commands."
        content = ctx.message.content
        for mention in when_mentioned(ctx.bot, ctx.message):
            if content.startswith(mention):
                trimmed_content = content[len(mention) :].strip()
                break
        else:
            return
        if ctx.invoked_parents or ctx.invoked_subcommand:  # pragma: no cover
            logger.warning("A CommandNotFound is being raised despite a subcommand being invoked.")
            return
        assert trimmed_content != "", "Trimmed content should not be empty."
        await ctx.defer()
        await self._show_search(ctx, scope=SearchTarget.builds, query=trimmed_content)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load search and build commands."""
    await bot.add_cog(SearchCog(bot))
