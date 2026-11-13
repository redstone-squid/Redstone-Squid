"""Create submission drafts from forms and consenting authors' build-log messages.

The form path retains supplied answers and attachments for the draft editor. The
message path delegates inference and candidate admission to ingestion.py. Both
paths use the shared submission services for finalization, so Discord collects
input and presents progress while those services decide when a build is ready.
"""

import logging
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, Self, override
from uuid import NAMESPACE_URL, uuid4, uuid5

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.consent import ensure_consented_account
from squid.bot.i18n import to_locale_str
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.draft_intake import receive_attachments
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.ingestion import ingest_message_bundle
from squid.bot.submission.input import optional_text, split_values
from squid.bot.submission.message_context import assemble_bundle
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui import controls as build_controls
from squid.bot.submission.ui.drafts import draft_editor, field_names
from squid.bot.ui import error_node, text_node
from squid.bot.utils.autocomplete import suggests
from squid.bot.utils.permissions import actor_for_interaction, current_edit_actor, enforce
from squid.bot.utils.sticky_message import StickyMessage
from squid.builds.application import (
    BuildInferenceService,
    BuildService,
)
from squid.builds.domain import BuildCategory, BuildDraft, DoorOrientationLiteral
from squid.builds.domain.models import BuildCategoryLiteral
from squid.core.i18n import tr
from squid.messages.application import MessageService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_RECALC
from squid.submissions.application import StoredDraft
from squid.submissions.application.prefill import prefill_submission
from squid.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    FormManifest,
    SubmissionOrigin,
)
from squid.submissions.errors import DraftCapacityExceededError
from squid_i18n import Text, md_tr
from squid_ui.text import md

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

# Disable the consent banner without disabling ingestion for consenting accounts.
# The bool annotation keeps type checkers from treating the guarded branches as unreachable.
CONSENT_STICKY_ENABLED: bool = False


class SavedDraftsScreen(sd.Screen):
    """Keep saved drafts and inference runs reachable across Discord message pages."""

    def __init__(self, nodes: Sequence[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]) -> None:
        self.nodes = tuple(nodes)

    @override
    def render(self) -> sl.Document[sl.ComponentsV2Target, sd.Request]:
        return sl.Document(self.nodes, key="saved-drafts")


class BuildSubmitCommands[BotT: squid.bot.app.RedstoneSquid](BuildCommandGroup[BotT]):
    """A cog with commands to submit builds."""

    bot: BotT
    builds: BuildService
    inference: BuildInferenceService
    messages: MessageService
    consent_sticky: StickyMessage

    @BuildCommandGroup.build_group.command(
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
                description=tr(t"submit-creator-ign-prompt = In-game names of the creators, comma separated."),
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
        request: sd.Request[Self],
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
            draft.creators_ign = split_values(creators)
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
        if stored.snapshot.revision == 0:
            prefill = await prefill_submission(draft, services.submission_forms, origin=SubmissionOrigin.DISCORD)
            unresolved = prefill.unresolved
            await services.submission_drafts.apply_change(
                draft_id,
                uploader_account_id,
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
            uploader_account_id,
            tuple(attachment for attachment in (file, file_2, file_3, file_4) if attachment is not None),
        )
        return await draft_editor(
            services,
            draft_id,
            changes=self.bot.topic_bus.topic("submission_draft", str(draft_id)),
            resolve_actor=partial(current_edit_actor, request),
            render_card=lambda build: render_build_card(
                build, self.bot.media_previews, schematic_summaries=self.bot.services.schematic_summaries
            ),
            notice=_unused_values_notice(unresolved, manifest, category_code),
        )

    @BuildCommandGroup.build_group.command(
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
    async def saved_drafts(self, request: sd.Request[Self], *, inbox: bool = False) -> sd.CommandResult:
        """Reopen saved drafts, or browse the staff correction inbox."""
        account_id = await ensure_consented_account(request, self.bot.services.accounts)
        if account_id is None:
            return None
        actor = await actor_for_interaction(request)
        drafts = (
            await self.bot.services.submission_drafts.attention_inbox(actor, limit=10)
            if inbox
            else await self.bot.services.submission_drafts.list_active(account_id)
        )
        runs = await self.bot.services.submission_inference.list_active(actor, inbox=inbox)
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.primitives.Section(
                (sl.primitives.Text(_draft_line(draft)),),
                sl.primitives.RoutedButton(
                    tr(t"build-drafts-open = Open"), build_controls.reopen_draft.id(draft_id=str(draft.snapshot.id))
                ),
            )
            for draft in drafts
        ]
        nodes.extend(
            sl.primitives.Section(
                (sl.primitives.Text(md_tr(t"build-drafts-detected = **Builds found in your build-log posts**")),),
                sl.primitives.RoutedButton(
                    tr(t"build-drafts-review = Review"), build_controls.reopen_inference.id(run_id=str(run_id))
                ),
            )
            for run_id in runs
        )
        if nodes:
            return SavedDraftsScreen(nodes)
        if inbox:
            return text_node(tr(t"build-drafts-inbox-empty = No drafts are waiting for staff correction."))
        return text_node(
            md_tr(
                t"build-drafts-empty = You have no drafts in progress. Start one with `/build submit`.",
            )
        )

    async def _is_build_log_message(self, message: discord.Message) -> bool:
        """Check the shared author/channel eligibility rule for ingestion and recalculation."""
        if message.author.bot or not isinstance(message.channel, discord.TextChannel):
            return False
        settings = await self.bot.services.settings.get_runtime()
        return message.channel.id in settings.build_log_channel_ids

    @sd.Cog.listener(name="on_message")
    async def infer_build_from_message(self, message: discord.Message) -> None:
        """Ingest an eligible build log message when its author has current consent."""
        if not await self._is_build_log_message(message):
            return
        assert isinstance(message.channel, discord.TextChannel)
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(message.author.id)
        )
        if account is None or account.id is None or account.needs_consent_refresh:
            logger.debug(
                "Skipping build inference for unconsented author %s in channel %s",
                message.author.id,
                message.channel.id,
            )
            if CONSENT_STICKY_ENABLED:
                await self.consent_sticky.trigger(message.channel)
            return

        if CONSENT_STICKY_ENABLED:
            self.consent_sticky.record_activity(message.channel.id)
        preceding = [item async for item in message.channel.history(before=message, limit=3)]
        preceding.reverse()
        try:
            run_id = await ingest_message_bundle(
                [message],
                preceding,
                self.bot.services,
                model=self.bot.inference_model,
                reasoning_effort=self.bot.inference_reasoning_effort,
            )
        except DraftCapacityExceededError:
            logger.warning(
                "Automated submission intake is full; skipped message %s in channel %s", message.id, message.channel.id
            )
            return
        await self.bot.refresh_posts("inference_run", str(run_id))

    @sd.context_menu(name=to_locale_str(tr(t"command-recalculate-build-name = Scan Build Message")), defer="private")
    async def recalc_context_menu(self, request: sd.Request[Self], message: discord.Message) -> sd.CommandResult:
        """Review proposed build corrections inferred from this message."""
        await enforce(request, BUILD_SUBMISSION_RECALC)
        if not await self._is_build_log_message(message):
            return error_node(
                tr(t"bot-submission-submit-nothing-to-recalculate = Nothing to recalculate"),
                tr(
                    t"submit-build-log-channel-required = Builds are only read "
                    t"out of messages posted in a build log channel."
                ),
            )

        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(message.author.id)
        )
        if account is None or account.id is None or account.needs_consent_refresh:
            user_id = message.author.id
            if CONSENT_STICKY_ENABLED and isinstance(message.channel, discord.TextChannel):
                await self.consent_sticky.trigger(message.channel)
            return error_node(
                tr(t"bot-submission-submit-author-has-not-consented = Author has not consented"),
                md_tr(
                    t"submit-author-consent-required = The author of this "
                    t"message ({(user_mention := sl.mention(user_id))}) has not consented to data storage. They must grant consent before "  # noqa: F841 - captured text identifier
                    t"this build can be ingested.",
                ),
            )

        return await self.propose_recalculation(request, message, owner_account_id=account.id)

    async def propose_recalculation(
        self,
        request: sd.Request[Self],
        message: discord.Message,
        *,
        owner_account_id: int,
    ) -> sd.CommandResult:
        """Retain inferred candidates and return review controls without submitting builds."""
        actor = await actor_for_interaction(request)
        run_id = (
            uuid5(NAMESPACE_URL, f"discord-recalculation:{request.interaction.id}")
            if request.interaction is not None
            else uuid4()
        )
        bundle = await assemble_bundle([message], preceding=(), include_images=True)
        candidates = await self.bot.services.submission_inference.infer(
            run_id,
            owner_account_id,
            bundle,
            purpose="recalculation",
            model=self.bot.inference_model,
            reasoning_effort=self.bot.inference_reasoning_effort,
        )
        proposals = [
            await self.bot.services.submission_recalculations.create(
                run_id=run_id,
                index=index,
                owner_account_id=owner_account_id,
                actor=actor,
                source_message_id=message.id,
                candidate=candidate.facts,
            )
            for index, candidate in enumerate(candidates)
        ]
        if not proposals:
            return text_node("No build candidates were inferred. Existing builds were preserved.")
        return tuple(
            sl.primitives.Section(
                (sl.primitives.Text(f"Recalculation candidate {index + 1}: review and choose its target build."),),
                sl.primitives.RoutedButton(
                    "Review changes", build_controls.reopen_revision.id(proposal_id=str(proposal.id))
                ),
            )
            for index, proposal in enumerate(proposals)
        )


def _draft_line(draft: StoredDraft) -> Text:
    """Describe a draft in the /build drafts list by category, a name, its state, and last edit."""
    answers = draft.snapshot.answers
    creators = answers.get("creators")
    name = answers.get("display_name") or (", ".join(map(str, creators)) if isinstance(creators, list) else None)
    category = draft.snapshot.category.capitalize()
    title = md(t"**{category}** · {name}") if name else md(t"**{category}**")
    match draft.snapshot.status:
        case DraftStatus.PROCESSING:
            state = tr(t"build-drafts-state-creating = Creating the build")
        case DraftStatus.NEEDS_ATTENTION if draft.preparation_retry_at is not None:
            state = tr(t"build-drafts-state-waiting = Waiting for files")
        case DraftStatus.NEEDS_ATTENTION:
            state = tr(t"build-drafts-state-needs-changes = Needs changes")
        case _:
            state = tr(t"build-drafts-state-editing = In progress")
    updated = draft.updated_at.to_stdlib()
    return Text.join("\n", (title, Text.join(" · ", (state, md_tr(t"build-drafts-edited = Edited {updated}")))))


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
