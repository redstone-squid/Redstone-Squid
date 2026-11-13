"""Discord commands for build search, browsing, editing, and submission."""

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Self, override

from discord.ext import commands
from discord.ext.commands import Context, when_mentioned

import squid_ui_discord as sd
from squid.bot.submission.build_browse import BuildBrowseScreen, BuildCapabilities
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.consent_banner import BuildLogConsentStickyMessage
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.records import RecordsScreen
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.utils.autocomplete import suggests
from squid.bot.utils.permissions import actor_for_interaction, allows, current_edit_actor
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_DEBUG,
    BUILD_SUBMISSION_EDIT,
    BUILD_SUBMISSION_REJECT,
    BUILD_SUBMISSION_VIEW_PENDING,
    RECORD_ENTRY_INSPECT,
    RECORD_ENTRY_REBUILD,
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

    @sd.command(
        name=tr(t"command-search-name = search"),
        description=tr(t"command-search-help = Search builds, records, and taxonomy metadata"),
        defer="public",
        options={
            "scope": sd.Option(
                name=tr(t"command-search-scope-name = scope"),
                description=tr(
                    t"build-search-target-types-help = What to look through: records, builds, patterns, restrictions, or all."
                ),
                choices=[
                    sd.Choice("records", tr(t"command-search-choice-scope-records = records")),
                    sd.Choice("builds", tr(t"command-search-choice-scope-builds = builds")),
                    sd.Choice("patterns", tr(t"command-search-choice-scope-patterns = patterns")),
                    sd.Choice("restrictions", tr(t"command-search-choice-scope-restrictions = restrictions")),
                    sd.Choice("everything", tr(t"command-search-choice-scope-everything = everything")),
                ],
            ),
            "sort": sd.Option(
                name=tr(t"command-search-sort-name = sort"),
                description=tr(t"build-search-sort-help = Order results by a field, ascending or descending."),
                autocomplete=suggests("search_sorts"),
            ),
            "mode": sd.Option(
                name=tr(t"command-search-mode-name = mode"),
                description=tr(
                    t"build-search-matching-mode-help = Use keyword matching or smart meaning-based matching."
                ),
                choices=[
                    sd.Choice("keyword", tr(t"command-search-choice-mode-keyword = keyword")),
                    sd.Choice("smart", tr(t"command-search-choice-mode-smart = smart")),
                ],
            ),
            "query": sd.Option(
                name=tr(t"command-search-query-name = query"),
                description=tr(t"build-search-query-help = Search text and filters, e.g. `width:5`."),
                autocomplete=suggests("search_query"),
            ),
        },
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

    @BuildCommandGroup.build_group.command(
        name=tr(t"command-build-browse-name = browse"),
        description=tr(t"command-build-browse-help = Browse builds and record holders."),
        options={
            "build_id": sd.Option(
                name=tr(t"command-build-browse-build-id-name = id"),
                description=tr(t"bot-submission-search-open-this-build-directly = Open this build directly."),
                autocomplete=suggests("builds"),
            ),
            "query": sd.Option(
                name=tr(t"command-build-browse-query-name = query"),
                description=tr(t"build-search-query-help = Search text and filters, e.g. `width:5`."),
            ),
        },
    )
    async def browse_builds(
        self, request: sd.Request[Self], build_id: int | None = None, query: str | None = None
    ) -> BuildBrowseScreen:
        """Browse builds and record holders."""
        screen = await self._catalogue(request, build_id=build_id)
        if query is not None:
            await screen._run_search(query, "")
        return screen

    @sd.command(
        name=tr(t"command-records-name = records"),
        description=tr(t"command-records-browse-help = Browse records and their holder builds"),
        options={
            "query": sd.Option(
                name=tr(t"command-records-query-name = query"),
                description=tr(
                    t"record-search-query-help = Name, keywords, or filters, e.g. kind:door base_title:smallest."
                ),
            )
        },
    )
    async def records_workspace(self, request: sd.Request[Self], query: str | None = None) -> BuildBrowseScreen:
        """Open the shared catalogue at its record standings."""
        screen = await self._catalogue(request, initial_records=True)
        if query is not None:
            await screen._run_search(query, "")
        return screen

    async def _catalogue(
        self,
        request: sd.Request[Self],
        *,
        build_id: int | None = None,
        initial_records: bool = False,
    ) -> BuildBrowseScreen:
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
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(request, node)

        async def refresh_posts(build_id: int) -> None:
            await self.bot.refresh_posts("build", str(build_id))

        can_inspect = request.guild is not None and await held(RECORD_ENTRY_INSPECT)
        can_rebuild = request.guild is not None and await held(RECORD_ENTRY_REBUILD)
        record_tools = (
            RecordsScreen(
                self.bot.services.records,
                self.bot.services.record_computation,
                can_inspect=can_inspect,
                can_rebuild=can_rebuild,
                authorize=authorize,
            )
            if can_inspect or can_rebuild
            else None
        )

        return BuildBrowseScreen(
            self.queries,
            self.builds,
            self.bot.services.edits,
            lambda: current_edit_actor(request),
            self.bot.services.schematics,
            topics=self.bot.topic_bus,
            initial_id=build_id,
            render_build=lambda build: render_build_card(build, self.bot.media_previews),
            capabilities=capabilities,
            actor_account_id=subject.account_id,
            authorize=authorize,
            refresh_posts=refresh_posts,
            records=self.bot.services.records,
            search=self.search,
            record_tools=record_tools,
            initial_records=initial_records,
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
