"""Discord commands for build search, browsing, editing, and submission."""

import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Self, override

import discord
from discord.ext import commands
from discord.ext.commands import Context, when_mentioned

import squid_ui.ext.discord as sd
from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.catalogue.search.contextual import SEARCH_COMMAND_NAME, SEARCH_SCOPE_NAME, search_autocomplete
from squid.bot.catalogue.search.entry import SearchTarget, open_search
from squid.bot.catalogue.search.results import SearchScreen
from squid.bot.i18n import to_locale_str
from squid.bot.submission.consent_banner import open_consent_prompt
from squid.bot.submission.form import open_submission_form
from squid.bot.submission.message_intake import BuildMessageIntake
from squid.bot.submission.ui.attachment_review import (
    AttachmentReview,
    review_attachments,
    warn_if_attachment_archive_unavailable,
)
from squid.bot.submission.ui.controls import edit_build, recover_build_editor, reopen_revision
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.submission.ui.inference import reopen_inference
from squid.bot.submission.ui.saved_drafts import SavedDraftsScreen
from squid.bot.utils.autocomplete import suggests
from squid.core.builds.domain import DoorOrientationLiteral
from squid.core.builds.domain.models import BuildCategoryLiteral
from squid.core.search.domain import SearchMode
from squid_i18n import md_tr, tr

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class SearchModeChoice(StrEnum):
    """User-facing names for search ranking modes."""

    keyword = "keyword"
    smart = "smart"


class BuildCommands[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Own build search, browsing, editing, submission, and consent-message lifetime."""

    # Declare the group and its members together so reloading this module creates
    # a fresh command tree instead of mutating a group owned by an imported base.
    build_group = sd.Group(
        name=to_locale_str(tr(t"command-build-name = build")),
        description=to_locale_str(tr(t"command-build-help = Browse and submit redstone builds")),
    )

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.intake = BuildMessageIntake(bot)

    @override
    async def cog_unload(self) -> None:
        await self.intake.close()

    @sd.Cog.listener(name="on_message")
    async def infer_build_from_message(self, message: discord.Message) -> None:
        """Process eligible build-log messages under the cog's lifetime."""
        await self.intake.infer_build_from_message(message)

    @sd.context_menu(name=to_locale_str(tr(t"command-recalculate-build-name = Scan Build Message")), defer="private")
    async def recalc_context_menu(self, request: sd.Request[Self, BotT], message: discord.Message) -> sd.CommandResult:
        """Review corrections inferred from the selected build-log message."""
        return await self.intake.recalculate(request, message)

    @sd.command(
        name=SEARCH_COMMAND_NAME,
        description=tr(t"command-search-help = Search builds, records, and taxonomy metadata"),
        options={
            "scope": sd.Option(
                name=SEARCH_SCOPE_NAME,
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
                description=md_tr(t"build-search-query-help = Search text and filters, e.g. `width:5`."),
                autocomplete=search_autocomplete,
            ),
        },
    )
    async def search_records(
        self,
        request: sd.Request[Self, BotT],
        scope: SearchTarget | None = None,
        sort: str | None = None,
        mode: SearchModeChoice = SearchModeChoice.keyword,
        *,
        query: str,
    ) -> BuildBrowseScreen | SearchScreen | None:
        """Search records, builds, patterns, and restrictions using text and field filters."""
        if request.interaction is not None and await request.client.contextual_searches.handle_command(
            request, query, scoped=scope is not None
        ):
            return None
        await request.defer("public")
        return await open_search(
            request,
            scope=scope,
            sort=sort,
            mode=SearchMode.LEXICAL if mode is SearchModeChoice.keyword else SearchMode.SEMANTIC,
            query=query,
        )

    @build_group.command(
        name=tr(t"command-build-issues-name = issues"),
        description=tr(t"command-build-issues-help = Review unresolved build attachments"),
        defer="private",
    )
    async def attachment_issues(self, request: sd.Request[Self, BotT]) -> AttachmentReview:
        return await AttachmentReview.open(request)

    @build_group.command(
        name=tr(t"command-build-browse-name = browse"),
        description=tr(t"command-build-browse-help = Browse builds and record holders."),
        defer="public",
        options={
            "build_id": sd.Option(
                name=tr(t"command-build-browse-build-id-name = id"),
                description=tr(t"bot-submission-search-open-this-build-directly = Open this build directly."),
                autocomplete=suggests("builds"),
            ),
        },
    )
    async def browse_builds(self, request: sd.Request[Self, BotT], build_id: int | None = None) -> sd.CommandResult:
        """Browse builds and record holders."""
        if build_id is None:
            return await BuildBrowseScreen.open_index(request)
        return await BuildBrowseScreen.open_detail(request, build_id=build_id)

    @sd.command(
        name=tr(t"command-records-name = records"),
        description=tr(t"command-records-browse-help = Browse records and their holder builds"),
        options={
            "query": sd.Option(
                name=tr(t"command-records-query-name = query"),
                description=tr(
                    t"record-search-query-help = Name, keywords, or filters, e.g. kind:door base_title:smallest."
                ),
                autocomplete=suggests("search_query"),
            )
        },
    )
    async def records_workspace(self, request: sd.Request[Self, BotT], query: str | None = None) -> BuildBrowseScreen:
        """Open the shared catalogue at its record standings."""
        if query is not None:
            return await BuildBrowseScreen.create_search(request, query=query, records=True)
        return await BuildBrowseScreen.create(request, initial_records=True)

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
        await request.respond(await open_search(request, query=trimmed_content))

    @build_group.command(
        name=tr(t"command-build-submit-name = submit"),
        defer="private",
        description=tr(
            t"command-build-submit-help = Submit a build. Every option is optional; fill in the rest in the form that opens."
        ),
        options={
            "category": sd.Option(
                name=tr(t"command-build-submit-category-name = category"),
                description=tr(t"submit-category-help = The kind of build to submit."),
                choices=[
                    sd.Choice("Door", tr(t"command-build-submit-choice-category-door = Door")),
                    sd.Choice("Extender", tr(t"command-build-submit-choice-category-extender = Extender")),
                    sd.Choice("Utility", tr(t"command-build-submit-choice-category-utility = Utility")),
                    sd.Choice("Entrance", tr(t"command-build-submit-choice-category-entrance = Entrance")),
                    sd.Choice("Other", tr(t"command-build-submit-choice-category-other = Other")),
                ],
            ),
            "name": sd.Option(
                name=tr(t"command-build-submit-name-name = name"),
                description=tr(t"submit-name-help = An optional name for the build."),
            ),
            "door_size": sd.Option(
                name=tr(t"command-build-submit-door-size-name = door_size"),
                description=md_tr(
                    t"submit-door-dimensions-help = Door size as width x height x depth, e.g. `2x2x1`. Depth is optional.",
                ),
            ),
            "door_type": sd.Option(
                name=tr(t"command-build-submit-door-type-name = door_type"),
                description=tr(t"bot-submission-submit-door-skydoor-or-trapdoor = Door, skydoor, or trapdoor."),
                choices=[
                    sd.Choice("Door", tr(t"command-build-submit-choice-door-type-door = Door")),
                    sd.Choice("Skydoor", tr(t"command-build-submit-choice-door-type-skydoor = Skydoor")),
                    sd.Choice("Trapdoor", tr(t"command-build-submit-choice-door-type-trapdoor = Trapdoor")),
                ],
            ),
            "build_size": sd.Option(
                name=tr(t"command-build-submit-build-size-name = build_size"),
                description=md_tr(
                    t"bot-submission-submit-the-whole-build-e-g-x-x = Build size as width x height x depth, e.g. `5x7x4`.",
                ),
            ),
            "creators": sd.Option(
                name=tr(t"command-build-submit-creators-name = creators"),
                description=tr(
                    t"submit-credit-reference-prompt = Credits, comma separated: Name, discord:@user, java:IGN, bedrock:Name."
                ),
                autocomplete=suggests("creators", multi=True),
            ),
            "versions": sd.Option(
                name=tr(t"command-build-submit-versions-name = versions"),
                description=md_tr(
                    t"submit-supported-versions-prompt = Minecraft versions it works in, e.g. `1.17-1.18.1, 1.20+`.",
                ),
                autocomplete=suggests("approved_source_versions"),
            ),
            "patterns": sd.Option(
                name=tr(t"command-build-submit-patterns-name = patterns"),
                description=md_tr(
                    t"submit-pattern-type-examples-help = Patterns, comma separated, e.g. `full lamp, funnel`.",
                ),
                autocomplete=suggests("approved_patterns", multi=True),
            ),
            "restrictions": sd.Option(
                name=tr(t"command-build-submit-restrictions-name = restrictions"),
                description=md_tr(
                    t"submit-comma-separated-examples-help = Restrictions, comma separated, e.g. `seamless, observerless`.",
                ),
                autocomplete=suggests("approved_restrictions", multi=True),
            ),
            "description": sd.Option(
                name=tr(t"command-build-submit-description-name = description"),
                description=tr(t"submit-staff-notes-prompt = A short description of the build."),
            ),
            "file": sd.Option(
                name=tr(t"command-build-submit-file-name = file"),
                description=tr(t"submit-attachment-types-help = Your schematic, an image, or a video."),
            ),
            "file_2": sd.Option(
                name=tr(t"command-build-submit-file-2-name = file_2"),
                description=tr(t"submit-another-attachment-help = Another schematic, image, or video."),
            ),
            "file_3": sd.Option(
                name=tr(t"command-build-submit-file-3-name = file_3"),
                description=tr(t"submit-another-attachment-help = Another schematic, image, or video."),
            ),
            "file_4": sd.Option(
                name=tr(t"command-build-submit-file-4-name = file_4"),
                description=tr(t"submit-another-attachment-help = Another schematic, image, or video."),
            ),
        },
    )
    async def submit_form(
        self,
        request: sd.Request[Self, BotT],
        *,
        category: BuildCategoryLiteral = "Door",
        name: str | None = None,
        door_size: str | None = None,
        door_type: DoorOrientationLiteral | None = None,
        build_size: str | None = None,
        creators: str | None = None,
        versions: str | None = None,
        patterns: str | None = None,
        restrictions: str | None = None,
        description: str | None = None,
        file: discord.Attachment | None = None,
        file_2: discord.Attachment | None = None,
        file_3: discord.Attachment | None = None,
        file_4: discord.Attachment | None = None,
    ) -> sd.CommandResult:
        """Open a retained draft with the supplied form values and attachments."""
        return await open_submission_form(
            request,
            category=category,
            name=name,
            door_size=door_size,
            door_type=door_type,
            build_size=build_size,
            creators=creators,
            versions=versions,
            patterns=patterns,
            restrictions=restrictions,
            description=description,
            file=file,
            file_2=file_2,
            file_3=file_3,
            file_4=file_4,
        )

    @build_group.command(
        name=tr(t"command-build-drafts-name = drafts"),
        defer="private",
        description=tr(t"command-build-drafts-help = Reopen saved drafts, or browse the staff correction inbox."),
        options={
            "inbox": sd.Option(
                name=tr(t"command-build-drafts-inbox-name = inbox"),
                description=tr(
                    t"command-build-drafts-inbox-help = Show drafts waiting for staff correction instead of your own."
                ),
            )
        },
    )
    async def saved_drafts(self, request: sd.Request[Self, BotT], *, inbox: bool = False) -> sd.CommandResult:
        """Reopen saved drafts, or browse the staff correction inbox."""
        return await SavedDraftsScreen.open_drafts(request, inbox=inbox)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load search and build commands."""
    await asyncio.to_thread(warn_if_attachment_archive_unavailable)
    await bot.add_cog(BuildCommands(bot))
    bot.ui_router.mount(
        __name__,
        BuildBrowseScreen.open_index,
        BuildBrowseScreen.open_detail,
        open_consent_prompt,
        review_attachments,
        edit_build,
        recover_build_editor,
        reopen_revision,
        DraftEditorScreen.open_draft,
        reopen_inference,
        SavedDraftsScreen.open_drafts,
    )


async def teardown(bot: squid.bot.app.RedstoneSquid) -> None:
    bot.ui_router.unmount(__name__)
