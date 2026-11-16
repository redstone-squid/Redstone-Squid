"""Discord commands for build search, browsing, editing, and submission."""

import logging
from collections.abc import Sequence
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Self, override
from uuid import NAMESPACE_URL, uuid4, uuid5

import discord
from discord.ext import commands
from discord.ext.commands import Context, when_mentioned

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.i18n import to_locale_str
from squid.bot.submission.build_browse import BuildBrowseScreen
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.consent_banner import BuildLogConsentStickyMessage
from squid.bot.submission.draft_intake import receive_attachments
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.input import optional_text, split_values
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.submit import BuildSubmitCommands, SavedDraftsScreen
from squid.bot.submission.ui.drafts import DraftEditorScreen, field_names
from squid.bot.ui import error_node
from squid.bot.utils.autocomplete import suggests
from squid.builds.domain import BuildCategory, BuildDraft, DoorOrientationLiteral
from squid.builds.domain.models import BuildCategoryLiteral
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort
from squid.submissions.application.credit_text import parse_credit_lines
from squid.submissions.application.prefill import prefill_submission
from squid.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    FieldOperation,
    FieldOperationKind,
    FormManifest,
    SubmissionOrigin,
)
from squid_i18n import md_tr, tr

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

    # Declare the group and its members together so reloading this module creates
    # a fresh command tree instead of mutating a group owned by an imported base.
    build_group = sd.Group(
        name=to_locale_str(tr(t"command-build-name = build")),
        description=to_locale_str(tr(t"command-build-help = Browse and submit redstone builds")),
    )

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.queries = bot.services.build_queries
        self.search = bot.services.search
        self.builds = bot.services.builds
        self.inference = bot.services.build_inference
        self.messages = bot.services.messages
        self.consent_sticky = BuildLogConsentStickyMessage(bot.background_tasks)

    @override
    async def cog_unload(self) -> None:
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
                description=md_tr(t"build-search-query-help = Search text and filters, e.g. `width:5`."),
                autocomplete=suggests("search_query"),
            ),
        },
    )
    async def search_records(
        self,
        request: sd.Request[Self, BotT],
        scope: SearchTarget = SearchTarget.builds,
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
        scope: SearchTarget = SearchTarget.builds,
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
            group_by_build=True,
        )
        page = await self.search.search(request)
        return SearchScreen(
            self.search,
            request,
            page,
            load_build=self.queries.get,
            render_build=lambda build: render_build_card(
                build, self.bot.media_previews, schematic_summaries=self.bot.services.schematic_summaries
            ),
        )

    @build_group.command(
        name=tr(t"command-build-browse-name = browse"),
        description=tr(t"command-build-browse-help = Browse builds and record holders."),
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
        screen = await BuildBrowseScreen.create(request, initial_records=True)
        if query is not None:
            await screen._run_search(query, "")
        return screen

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
        """Submit a build. Every option is optional; fill in the rest in the form that opens."""
        # Obtain consent before creating a draft or downloading attachments.
        uploader_account_id = await ensure_consented_account(request, self.bot.services.accounts)
        if uploader_account_id is None:
            return None

        if category != "Door" and (door_size is not None or door_type is not None):
            return error_node(
                tr(t"submit-inapplicable-options = Options do not apply"),
                tr(t"submit-door-options-only = Door size and orientation can only be supplied for doors."),
            )
        if category not in {"Door", "Extender"} and patterns is not None:
            return error_node(
                tr(t"submit-inapplicable-options = Options do not apply"),
                tr(t"submit-pattern-options-only = Patterns can only be supplied for doors and extenders."),
            )
        category_code = category.lower()
        draft = BuildDraft(
            ai_generated=False,
            category=BuildCategory(category),
            display_name=optional_text(name) if name is not None else None,
        )
        if door_size is not None:
            try:
                draft.door_dimensions = parse_hallway_dimensions(door_size)
            except ValueError:
                return error_node(
                    tr(t"bot-submission-submit-check-door-size = Check the door size"),
                    md_tr(
                        t"submit-door-size-format = Write it as width x height x depth, such as `2x2x1` or `2x2`.",
                    ),
                )
        if build_size is not None:
            try:
                draft.dimensions = parse_dimensions(build_size)
            except ValueError:
                return error_node(
                    tr(t"bot-submission-submit-check-build-size = Check the build size"),
                    md_tr(
                        t"submit-build-size-format = Write it as width x height x depth, such as `5x7x4`.",
                    ),
                )
        if door_type is not None:
            draft.door_orientation = door_type
        if patterns is not None:
            draft.patterns = split_values(patterns)
        if versions is not None:
            draft.version_spec = optional_text(versions)
        if creators is not None:
            draft.credits = parse_credit_lines("\n".join(split_values(creators)))
        if restrictions is not None:
            await self.builds.classify_restrictions(draft, split_values(restrictions))
        if description is not None and (parsed_description := optional_text(description)) is not None:
            draft.description = parsed_description

        services = self.bot.services
        manifest = await services.submission_forms.manifest(locale=None)
        # Derive the draft ID from the interaction so a retried command reopens the same draft.
        draft_id = (
            uuid5(NAMESPACE_URL, f"discord-submission:{request.interaction.id}")
            if request.interaction is not None
            else uuid4()
        )
        stored = await services.submission_drafts.create(
            draft_id=draft_id,
            owner_account_id=uploader_account_id,
            category=category_code,
            origin=SubmissionOrigin.DISCORD,
            client_capabilities=frozenset(
                field.required_capability
                for field in manifest.fields_for(category_code)
                if field.required_capability is not None
            ),
            locale=None,
        )
        unresolved: tuple[str, ...] = ()
        actor = await request.client.actors.resolve(request, refresh=True)
        if stored.snapshot.revision == 0:
            prefill = await prefill_submission(draft, services.submission_forms, origin=SubmissionOrigin.DISCORD)
            unresolved = prefill.unresolved
            await services.submission_drafts.apply_change(
                draft_id,
                actor,
                DraftChange(
                    0,
                    "discord-intake",
                    DraftChangeKey(f"initial:{draft_id}"),
                    tuple(
                        FieldOperation(uuid4(), key, FieldOperationKind.SET, value)
                        for key, value in prefill.answers.items()
                    ),
                ),
                locale=None,
            )
        await receive_attachments(
            services,
            draft_id,
            actor,
            tuple(attachment for attachment in (file, file_2, file_3, file_4) if attachment is not None),
        )
        return await DraftEditorScreen.load(
            services,
            draft_id,
            changes=self.bot.topic_bus.topic("submission_draft", str(draft_id)),
            resolve_actor=partial(request.client.actors.resolve, request, refresh=True),
            render_card=lambda build: render_build_card(
                build, self.bot.media_previews, schematic_summaries=self.bot.services.schematic_summaries
            ),
            notice=_unused_values_notice(unresolved, manifest, category_code),
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


def _unused_values_notice(field_ids: Sequence[str], manifest: FormManifest, category: str) -> sl.TextLike:
    """Name the command options whose values the form could not accept, or return empty text.

    Prefill drops values the form rejects, such as a "5 wide" door whose height and depth are
    "not applicable", so the submitter can fill those fields in again.
    """
    if not field_ids:
        return ""
    fields = field_names(manifest, category, field_ids)
    return md_tr(
        t"draft-editor-unused-options = Some values from the command could not be used: {fields}. Fill them in below.",
    )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load search and build commands."""
    await bot.add_cog(BuildCommands(bot))
