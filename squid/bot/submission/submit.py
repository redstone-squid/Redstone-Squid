"""A cog with commands to submit builds."""

import logging
from typing import TYPE_CHECKING, Self

import discord
from discord import Message, app_commands

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.consent import ensure_consented_account
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.ingestion import ingest_message_bundle
from squid.bot.submission.input import optional_text, split_values
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.ui import error_node, text_node
from squid.bot.utils.autocomplete import autocompletes, suggests
from squid.bot.utils.permissions import enforce
from squid.bot.utils.sticky_message import StickyMessage
from squid.builds.application import (
    BuildInferenceService,
    BuildService,
)
from squid.builds.domain import BuildCategory, BuildDraft, DoorOrientationLiteral
from squid.core.i18n import tr
from squid.messages.application import MessageService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_RECALC
from squid.submissions.errors import DraftCapacityExceededError

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

# Kill switch while ingestion is not live yet; flip to True to bring the sticky back.
# Typed `bool`, not the inferred `Literal[False]`, so the guarded branches are not unreachable.
CONSENT_STICKY_ENABLED: bool = False

# TODO: Set up a webhook for the bot to handle google form submissions.


class BuildSubmitCommands[BotT: "squid.bot.app.RedstoneSquid"](BuildCommandGroup[BotT]):
    """A cog with commands to submit builds."""

    bot: BotT
    builds: BuildService
    inference: BuildInferenceService
    messages: MessageService
    consent_sticky: StickyMessage

    @autocompletes(
        pattern=suggests("approved_patterns", multi=True),
        versions="approved_source_versions",
        restrictions=suggests("approved_restrictions", multi=True),
        creators=suggests("creators", multi=True),
    )
    @BuildCommandGroup.build_group.command(name="submit", defer="private")
    @app_commands.describe(
        door_size=app_commands.locale_str("The door opening, e.g. `2x2`. Width x height (x depth)."),
        door_type=app_commands.locale_str("Door, Skydoor, or Trapdoor."),
        pattern=app_commands.locale_str("Pattern types, comma separated. For example: full lamp, funnel."),
        build_size=app_commands.locale_str("The whole build, e.g. `5x7x4`. Width x height (x depth)."),
        versions=app_commands.locale_str("Versions the build works in, like `1.17 - 1.18.1, 1.20+`."),
        restrictions=app_commands.locale_str("Comma separated, e.g. `Seamless, Observerless`. See `/help`."),
        creators=app_commands.locale_str("In-game names of the creator(s), comma separated."),
        notes=app_commands.locale_str("Anything staff should know about the build."),
        first_attachment=app_commands.locale_str("An image, video, or schematic; sorted out automatically."),
        second_attachment=app_commands.locale_str("An image, video, or schematic; sorted out automatically."),
        third_attachment=app_commands.locale_str("An image, video, or schematic; sorted out automatically."),
        fourth_attachment=app_commands.locale_str("An image, video, or schematic; sorted out automatically."),
    )
    async def submit_form(
        self,
        request: sd.Request[Self],
        *,
        door_size: str | None = None,
        door_type: DoorOrientationLiteral | None = None,
        pattern: str | None = None,
        build_size: str | None = None,
        versions: str | None = None,
        restrictions: str | None = None,
        creators: str | None = None,
        notes: str | None = None,
        first_attachment: discord.Attachment | None = None,
        second_attachment: discord.Attachment | None = None,
        third_attachment: discord.Attachment | None = None,
        fourth_attachment: discord.Attachment | None = None,
    ) -> sd.CommandResult:
        """Submit a build. Every field is optional; a guided form picks up whatever you skip."""
        # Before the uploads, not after: declining should not cost the user an attachment round
        # trip, and the notice describes exactly what submitting a build publishes.
        uploader_account_id = await ensure_consented_account(request, self.bot.services.accounts)
        if uploader_account_id is None:
            return None

        draft = BuildDraft(ai_generated=False, category=BuildCategory.DOOR)
        try:
            if door_size is not None:
                draft.door_dimensions = parse_hallway_dimensions(door_size)
            if build_size is not None:
                draft.dimensions = parse_dimensions(build_size)
        except ValueError as error:
            return error_node(tr(t"Check the dimensions"), str(error))

        if door_type is not None:
            draft.door_orientation = door_type
        if pattern is not None:
            draft.patterns = split_values(pattern)
        if versions is not None:
            draft.version_spec = optional_text(versions)
        if creators is not None:
            draft.creators_ign = split_values(creators)
        if restrictions is not None:
            await self.builds.classify_restrictions(draft, split_values(restrictions))
        if notes is not None and (parsed_notes := optional_text(notes)) is not None:
            draft.extra_info["user"] = parsed_notes

        from uuid import NAMESPACE_URL, uuid4, uuid5

        from squid.bot.submission.draft_intake import receive_attachments
        from squid.bot.submission.ui.drafts import draft_editor
        from squid.submissions.application.prefill import prefill_submission
        from squid.submissions.domain import (
            DraftChange,
            DraftChangeKey,
            FieldOperation,
            FieldOperationKind,
            SubmissionOrigin,
        )

        manifest = await self.bot.services.submission_forms.manifest(locale=None)
        draft_id = (
            uuid5(NAMESPACE_URL, f"discord-submission:{request.interaction.id}")
            if request.interaction is not None
            else uuid4()
        )
        stored = await self.bot.services.submission_drafts.create(
            draft_id=draft_id,
            owner_account_id=uploader_account_id,
            category="door",
            origin=SubmissionOrigin.DISCORD,
            client_capabilities=frozenset(
                field.required_capability
                for field in manifest.fields_for("door")
                if field.required_capability is not None
            ),
            locale=None,
        )
        if stored.snapshot.revision == 0:
            prefill = await prefill_submission(
                draft, self.bot.services.submission_forms, origin=SubmissionOrigin.DISCORD
            )
            await self.bot.services.submission_drafts.apply_change(
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
            self.bot.services,
            draft_id,
            uploader_account_id,
            tuple(
                attachment
                for attachment in (first_attachment, second_attachment, third_attachment, fourth_attachment)
                if attachment is not None
            ),
        )
        return await draft_editor(self.bot.services, uploader_account_id, draft_id)

    @BuildCommandGroup.build_group.command(name="drafts", defer="private")
    async def saved_drafts(self, request: sd.Request[Self], *, inbox: bool = False) -> sd.CommandResult:
        """Reopen saved drafts, or browse the staff correction inbox."""
        import squid_ui as sl
        from squid.bot.submission.ui.controls import draft_reopen

        actor = await ensure_consented_account(request, self.bot.services.accounts)
        if actor is None:
            return None
        drafts = (
            await self.bot.services.submission_drafts.attention_inbox(actor, limit=10)
            if inbox
            else await self.bot.services.submission_drafts.list_active(actor)
        )
        from squid.bot.submission.ui.controls import inference_reopen
        from squid.bot.utils.permissions import subject_for_interaction

        runs = await self.bot.services.submission_inference.list_active(
            await subject_for_interaction(request), inbox=inbox
        )
        nodes = [
            sl.primitives.Section(
                (sl.primitives.Text(f"{draft.snapshot.category}: {draft.snapshot.status.value.replace('_', ' ')}"),),
                sl.primitives.RoutedButton("Open draft", draft_reopen.id(draft_id=str(draft.snapshot.id))),
            )
            for draft in drafts
        ]
        nodes.extend(
            sl.primitives.Section(
                (sl.primitives.Text("Retained inference candidates"),),
                sl.primitives.RoutedButton("Review candidates", inference_reopen.id(run_id=str(run_id))),
            )
            for run_id in runs
        )
        return tuple(nodes) if nodes else text_node("No active drafts or inference runs.")

    def _is_build_log_message(self, message: Message) -> bool:
        """Whether inference has anything to read this message for.

        Split out of the listener so the right-click can say "not a build log message" instead
        of reporting a recalculation that never ran.
        """
        return (
            not message.author.bot
            and isinstance(message.channel, discord.TextChannel)
            and message.channel.id in self.bot.community_config.build_log_channel_ids
        )

    @sd.Cog.listener(name="on_message")
    async def infer_build_from_message(self, message: Message):
        """Infer a build from a message."""
        if not self._is_build_log_message(message):
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
            await message.channel.send(
                "Automated submission intake is full. Use `/build drafts` to finish or discard retained work, "
                "then post this submission again.",
                reference=message,
                allowed_mentions=discord.AllowedMentions(replied_user=True),
            )
            return
        await self.bot.refresh_posts("inference_run", str(run_id))

    @sd.context_menu(name="Recalculate Build", defer="private")
    async def recalc_context_menu(self, request: sd.Request[Self], message: discord.Message) -> sd.CommandResult:
        """Re-read a build out of the message that was right-clicked.

        This was `/build recalc <message>`, which in slash form meant copying a link to a
        message and pasting it back at the bot (audit C4). Inference is a judgement about one
        specific message, which is what a message context menu is.
        """
        # A context menu cannot carry `requires(...)`, so the same denial is raised by hand.
        await enforce(request, BUILD_SUBMISSION_RECALC)
        if not self._is_build_log_message(message):
            return error_node(
                tr(t"Nothing to recalculate"),
                tr(t"Builds are only read out of messages posted in a build log channel."),
            )

        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(message.author.id)
        )
        if account is None or account.id is None or account.needs_consent_refresh:
            user_id = message.author.id
            if CONSENT_STICKY_ENABLED and isinstance(message.channel, discord.TextChannel):
                await self.consent_sticky.trigger(message.channel)
            return error_node(
                tr(t"Author has not consented"),
                tr(
                    t"The author of this message (<@{user_id}>) has not consented to data storage. "
                    t"They must grant consent before this build can be ingested."
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
        from uuid import NAMESPACE_URL, uuid4, uuid5

        import squid_ui as sl
        from squid.bot.submission.message_context import assemble_bundle
        from squid.bot.submission.ui.controls import revision_reopen
        from squid.bot.utils.permissions import subject_for_interaction

        actor = await subject_for_interaction(request)
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
            await self.bot.services.submission_revisions.create(
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
                sl.primitives.RoutedButton("Review changes", revision_reopen.id(proposal_id=str(proposal.id))),
            )
            for index, proposal in enumerate(proposals)
        )
