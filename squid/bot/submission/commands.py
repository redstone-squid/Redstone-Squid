"""Discord commands for build search, browsing, editing, and submission."""

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Self, override

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, when_mentioned

import squid_ui_discord as sd
from squid.bot.i18n import to_locale_str
from squid.bot.submission.build_browse import BuildBrowseScreen, BuildCapabilities
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.consent_banner import BuildLogConsentStickyMessage
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import actor_for_interaction, allows
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SCHEMATIC_MEASURE_TIMING,
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_DEBUG,
    BUILD_SUBMISSION_EDIT,
    BUILD_SUBMISSION_REJECT,
    BUILD_SUBMISSION_VIEW_PENDING,
)
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort
from squid_i18n import tr

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


class BuildCommands[BotT: squid.bot.app.RedstoneSquid](
    BuildEditCommands[BotT],
    BuildSubmitCommands[BotT],
):
    """Own build search, browsing, editing, submission, and consent-message lifetime."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.queries = bot.services.build_queries
        self.search = bot.services.search
        self.builds = bot.services.builds
        self.inference = bot.services.build_inference
        self.messages = bot.services.messages
        self.consent_sticky = BuildLogConsentStickyMessage(bot.background_tasks)

    @override
    async def ui_unload(self) -> None:
        await self.consent_sticky.close()

    @autocompletes(sort="search_sorts", query="search_query")
    @sd.command(
        name=to_locale_str(tr(t"command-search-name = search")),
        description=to_locale_str(tr(t"command-search-help = Search builds, records, and taxonomy metadata")),
        defer="public",
    )
    @app_commands.rename(
        mode=to_locale_str(tr(t"command-search-mode-name = mode")),
        query=to_locale_str(tr(t"command-search-query-name = query")),
        scope=to_locale_str(tr(t"command-search-scope-name = scope")),
        sort=to_locale_str(tr(t"command-search-sort-name = sort")),
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name=to_locale_str(tr(t"command-search-choice-mode-keyword = keyword")), value="keyword"
            ),
            app_commands.Choice(name=to_locale_str(tr(t"command-search-choice-mode-smart = smart")), value="smart"),
        ],
        scope=[
            app_commands.Choice(
                name=to_locale_str(tr(t"command-search-choice-scope-records = records")), value="records"
            ),
            app_commands.Choice(name=to_locale_str(tr(t"command-search-choice-scope-builds = builds")), value="builds"),
            app_commands.Choice(
                name=to_locale_str(tr(t"command-search-choice-scope-patterns = patterns")), value="patterns"
            ),
            app_commands.Choice(
                name=to_locale_str(tr(t"command-search-choice-scope-restrictions = restrictions")), value="restrictions"
            ),
            app_commands.Choice(
                name=to_locale_str(tr(t"command-search-choice-scope-everything = everything")), value="everything"
            ),
        ],
    )
    @app_commands.describe(
        query=to_locale_str(tr(t"build-search-query-help = Search text and filters, e.g. `width:5`.")),
        scope=to_locale_str(
            tr(
                t"build-search-target-types-help = What to look through: "
                t"records, builds, patterns, restrictions, or all."
            )
        ),
        sort=to_locale_str(tr(t"build-search-sort-help = Order results by a field, ascending or descending.")),
        mode=to_locale_str(
            tr(t"build-search-matching-mode-help = Use keyword matching or smart meaning-based matching.")
        ),
    )
    async def search_records(
        self,
        request: sd.Request[Self],
        scope: SearchTarget = SearchTarget.records,
        sort: str | None = None,
        mode: SearchModeChoice = SearchModeChoice.keyword,
        *,
        query: str,
    ) -> SearchScreen:
        """Search records, builds, patterns, and restrictions using text and field filters."""
        return await self._search_screen(scope=scope, sort=sort, mode=mode, query=query)

    async def _search_screen(
        self,
        *,
        scope: SearchTarget = SearchTarget.records,
        sort: str | None = None,
        mode: SearchModeChoice = SearchModeChoice.keyword,
        query: str,
    ) -> SearchScreen:
        search_scope, targeted_query = _targeted(scope, query)
        request = SearchRequest(
            targeted_query,
            scope=search_scope,
            mode=SearchMode.LEXICAL if mode is SearchModeChoice.keyword else SearchMode.SEMANTIC,
            sort=SearchSort.parse(sort),
        )
        page = await self.search.search(request)
        return SearchScreen(
            self.search,
            request,
            page,
            load_build=self.queries.get,
            render_build=lambda build: render_build_card(build, self.bot.media_previews),
        )

    @autocompletes(build_id="builds")
    @BuildCommandGroup.build_group.command(
        name=to_locale_str(tr(t"command-build-browse-name = browse")),
        description=to_locale_str(
            tr(t"command-build-browse-help = Browse searchable build details, review actions, and schematic tools.")
        ),
    )
    @app_commands.rename(build_id=to_locale_str(tr(t"command-build-browse-build-id-name = id")))
    @app_commands.describe(
        build_id=to_locale_str(tr(t"bot-submission-search-open-this-build-directly = Open this build directly."))
    )
    async def browse_builds(self, request: sd.Request[Self], build_id: int | None = None) -> BuildBrowseScreen:
        """Browse searchable build details, review actions, and schematic tools."""
        subject = await actor_for_interaction(request)
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
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(request, node)

        async def refresh_posts(build_id: int) -> None:
            await self.bot.refresh_posts("build", str(build_id))

        return BuildBrowseScreen(
            self.queries,
            self.builds,
            self.bot.services.schematics,
            topics=self.bot.topic_bus,
            initial_id=build_id,
            render_build=lambda build: render_build_card(build, self.bot.media_previews),
            capabilities=capabilities,
            actor_account_id=subject.account_id,
            authorize=authorize,
            refresh_posts=refresh_posts,
        )

    @sd.Cog.listener("on_command_error")
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
        request = await self.ui.request(ctx)
        await request.defer("public")
        await request.respond(await self._search_screen(scope=SearchTarget.builds, query=trimmed_content))


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load search and build commands."""
    await bot.add_cog(BuildCommands(bot))
