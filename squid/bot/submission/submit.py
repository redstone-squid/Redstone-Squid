"""A cog with commands to submit builds."""

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

import discord
from discord import Message, app_commands

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.consent import ensure_consented_account
from squid.bot.submission.attachments import AttachmentKind, classify_attachment
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.ingestion import ingest_message_bundle
from squid.bot.submission.input import optional_text, split_values
from squid.bot.submission.media import CatboxMirror
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.views import SubmissionDeliveryError, SubmissionOutcome, SubmissionScreen
from squid.bot.ui import error_node, text_node
from squid.bot.utils.autocomplete import autocompletes, suggests
from squid.bot.utils.permissions import enforce
from squid.bot.utils.sticky_message import StickyMessage
from squid.builds.application import (
    BuildInferenceService,
    BuildService,
)
from squid.builds.domain import Build, BuildDraft, DoorOrientationLiteral
from squid.core.errors import SquidError
from squid.core.i18n import tr
from squid.messages.application import MessageService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_RECALC
from squid.schematics.application import IngestedSchematic, IngestRequest

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

        draft = BuildDraft(ai_generated=False)
        try:
            if door_size is not None:
                draft.door_dimensions = parse_hallway_dimensions(door_size)
            if build_size is not None:
                draft.dimensions = parse_dimensions(build_size)
        except ValueError as error:
            return error_node(tr("Check the dimensions"), str(error))
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

        attachments = [first_attachment, second_attachment, third_attachment, fourth_attachment]
        schematics = self.bot.services.schematics

        async def _handle_attachment(
            attachment: discord.Attachment | None,
        ) -> tuple[AttachmentKind, str, bytes] | None:
            if attachment is None:
                return None
            classified = classify_attachment(
                attachment.filename,
                attachment.content_type,
                attachment.size,
                max_bytes=schematics.limits.max_upload_bytes,
            )
            data = await attachment.read()
            if classified.kind == "schematic":
                return classified.kind, classified.filename, data
            url = await self.bot.catbox.upload(classified.filename, data, classified.content_type)
            return classified.kind, url, data

        uploaded_media = await asyncio.gather(*(_handle_attachment(attachment) for attachment in attachments))
        pending_schematics: list[tuple[str, bytes]] = []
        for uploaded in uploaded_media:
            if uploaded is None:
                continue
            kind, url, data = uploaded
            if kind == "image":
                draft.add_link("image", url)
            elif kind == "video":
                draft.add_link("video", url)
            else:
                pending_schematics.append((url, data))

        # Prefilling only fills a gap: a declared build size is never overwritten, because a
        # schematic export is frequently cropped to the mechanism and legitimately smaller than
        # the build a person measured. The form shows the prefill as an editable default, and
        # whatever the human submits wins from that point on.
        analyses = await self._analyse_attachments(pending_schematics, uploader_account_id=uploader_account_id)
        if analyses and not any(item is not None for item in draft.dimensions):
            measured = analyses[0][1].analysis.metrics.dimensions
            draft.dimensions = (measured.width, measured.height, measured.length)

        async def persist_draft() -> SubmissionOutcome:
            """Commit the draft from inside the form's submit button.

            Anything raised here leaves the workspace message alive and clickable, so the user
            retries from the draft they already filled in instead of rerunning the command.
            """
            build = draft.finalize()
            self._note_dimension_mismatch(build, analyses)
            await self._note_schematic_duplicates(build, analyses)
            await self.builds.submit(build, submitter_account_id=uploader_account_id, ai_generated=False)
            await self._record_analyses(build, analyses, uploader_account_id=uploader_account_id)
            handler = self.bot.for_build(build)
            try:
                node = await handler.render_node()
                await handler.post_for_voting()
            except Exception as error:
                fallback = text_node(tr("Submission saved. The review card still needs delivery."))
                raise SubmissionDeliveryError(SubmissionOutcome(build, fallback, delivery_complete=False)) from error
            return SubmissionOutcome(build, node)

        return SubmissionScreen(draft, self.builds, on_submit=persist_draft)

    async def _analyse_attachments(
        self, pending: Sequence[tuple[str, bytes]], *, uploader_account_id: int
    ) -> list[tuple[IngestRequest, IngestedSchematic]]:
        """Analyze uploaded schematics, dropping any the engine cannot read.

        A schematic is enrichment, not a prerequisite: a corrupt file, a missing engine, or a
        crashed worker must leave the submission itself working, so every failure here is
        logged and skipped rather than raised at the user mid-form.
        """
        schematics = self.bot.services.schematics
        if not schematics.available:
            return []

        analysed: list[tuple[IngestRequest, IngestedSchematic]] = []
        for filename, data in pending:
            request = IngestRequest(data=data, filename=filename, uploaded_by_account_id=uploader_account_id)
            try:
                analysed.append((request, await schematics.ingest(request)))
            except SquidError:
                logger.warning("Could not analyze the attached schematic %s.", filename, exc_info=True)
        return analysed

    async def _record_analyses(
        self, build: Build, analyses: Sequence[tuple[IngestRequest, IngestedSchematic]], *, uploader_account_id: int
    ) -> None:
        """Persist the analyses now that the build has an id. The first upload is primary."""
        if build.id is None or not analyses:
            return
        schematics = self.bot.services.schematics
        for index, (request, ingested) in enumerate(analyses):
            try:
                await schematics.record(build.id, ingested, request, primary=index == 0)
            except SquidError:
                logger.warning(
                    "Could not record the schematic analysis for build %s.",
                    build.id,
                    exc_info=True,
                    extra={
                        "squid.build.id": build.id,
                        "squid.schematic.format": ingested.analysis.metrics.source_format.value,
                    },
                )

    async def _note_schematic_duplicates(
        self,
        build: Build,
        analyses: Sequence[tuple[IngestRequest, IngestedSchematic]],
    ) -> None:
        """Retain duplicate evidence before the build row is persisted."""
        if not analyses:
            return
        try:
            duplicates = await self.bot.services.schematics.find_duplicates(analyses[0][1])
        except SquidError:
            logger.warning(
                "Could not check the submitted schematic for duplicates.",
                exc_info=True,
                extra={"squid.schematic.format": analyses[0][1].analysis.metrics.source_format.value},
            )
            return
        if duplicates:
            build.extra_info["schematic_duplicates"] = [
                {
                    "build_id": candidate.build_id,
                    "tier": candidate.tier,
                    "footprint_distance": candidate.footprint_distance,
                }
                for candidate in duplicates
            ]

    @staticmethod
    def _note_dimension_mismatch(build: Build, analyses: Sequence[tuple[IngestRequest, IngestedSchematic]]) -> None:
        """Record, but never silently resolve, a disagreement between human and file.

        The declared value wins: a schematic export is frequently cropped to the mechanism and
        legitimately smaller than the build a person measured. Overwriting it would corrupt the
        record, so the discrepancy is surfaced as visible evidence for the reviewers instead.
        """
        if not analyses:
            return
        measured = analyses[0][1].analysis.metrics.dimensions
        declared = (build.width, build.height, build.depth)
        if None in declared or declared == (measured.width, measured.height, measured.length):
            return
        build.extra_info["schematic_dimension_mismatch"] = (
            f"Declared {declared[0]}x{declared[1]}x{declared[2]}, "
            f"schematic measures {measured.width}x{measured.height}x{measured.length}"
        )

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
        builds = await ingest_message_bundle(
            [message],
            preceding,
            self.bot.services,
            model=self.bot.inference_model,
            reasoning_effort=self.bot.inference_reasoning_effort,
            mirror=CatboxMirror(self.bot.catbox),
        )
        for build in builds:
            await self.bot.for_build(build).post_for_voting(type="add")

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
                tr("Nothing to recalculate"),
                tr("Builds are only read out of messages posted in a build log channel."),
            )

        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(message.author.id)
        )
        if account is None or account.id is None or account.needs_consent_refresh:
            if CONSENT_STICKY_ENABLED and isinstance(message.channel, discord.TextChannel):
                await self.consent_sticky.trigger(message.channel)
            return error_node(
                tr("Author has not consented"),
                tr(
                    "The author of this message (<@{user_id}>) has not consented to data storage. "
                    "They must grant consent before this build can be ingested.",
                    user_id=message.author.id,
                ),
            )

        await self.infer_build_from_message(message)
        return text_node(tr("Build recalculated."))
