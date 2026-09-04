"""A cog with commands to submit builds."""

import logging
from typing import TYPE_CHECKING, Self

import discord
from discord import Message, app_commands

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.consent import ensure_consented_account
from squid.bot.submission.attachment_enrichment import (
    AttachmentFailure,
    AttachmentLifecycle,
    attachment_failure_evidence,
    attachment_failure_for,
    default_only_usable,
    merge_duplicate_evidence,
    primary_schematic,
)
from squid.bot.submission.attachments import classify_attachment
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
from squid.builds.domain.models import AttachmentFailureInfo
from squid.core.errors import SquidError
from squid.core.i18n import tr
from squid.messages.application import MessageService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_RECALC
from squid.schematics.application import IngestRequest

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

        supplied = (first_attachment, second_attachment, third_attachment, fourth_attachment)
        prepared = [
            await self._prepare_attachment(attachment, uploader_account_id=uploader_account_id)
            for attachment in supplied
            if attachment is not None
        ]
        attachments = default_only_usable(prepared)
        for attachment in attachments:
            if attachment.media_url is None or attachment.classification is None:
                continue
            draft.add_link(attachment.classification.kind, attachment.media_url)
        self._note_attachment_failures(draft, attachments)

        async def persist_draft(selected_attachments: tuple[AttachmentLifecycle, ...]) -> SubmissionOutcome:
            return await self._persist_draft(draft, selected_attachments, uploader_account_id=uploader_account_id)

        return SubmissionScreen(
            draft,
            self.builds,
            attachments=attachments,
            on_submit=persist_draft,
        )

    async def _persist_draft(
        self,
        draft: BuildDraft,
        attachments: tuple[AttachmentLifecycle, ...],
        *,
        uploader_account_id: int,
    ) -> SubmissionOutcome:
        """Persist one completed form, then finish its recoverable enrichment and delivery."""
        build = draft.finalize()
        self._note_dimension_mismatch(build, attachments)
        await self._note_schematic_duplicates(build, attachments)
        await self.builds.submit(build, submitter_account_id=uploader_account_id, ai_generated=False)

        try:
            if failures := await self._record_analyses(build, attachments):
                self._append_attachment_failures(build, failures)
                await self.builds.save(build)
            handler = self.bot.for_build(build)
            node = await handler.render_node()
            await handler.post_for_voting()
        except Exception as error:
            fallback = text_node(
                tr(t"Submission saved. Attachment processing or review-card delivery still needs recovery.")
            )
            raise SubmissionDeliveryError(SubmissionOutcome(build, fallback, delivery_complete=False)) from error
        return SubmissionOutcome(build, node)

    async def _prepare_attachment(
        self, attachment: discord.Attachment, *, uploader_account_id: int
    ) -> AttachmentLifecycle:
        """Classify and enrich one attachment without aborting its siblings."""
        schematics = self.bot.services.schematics
        identity = str(attachment.id)
        try:
            classified = classify_attachment(
                attachment.filename,
                attachment.content_type,
                attachment.size,
                max_bytes=schematics.limits.max_upload_bytes,
            )
        except SquidError as error:
            return AttachmentLifecycle(
                identity,
                attachment.filename,
                failure=AttachmentFailure("classification", error.public_detail()),
            )
        try:
            data = await attachment.read()
        except discord.HTTPException, OSError:
            logger.warning("Could not download attachment %s.", classified.filename, exc_info=True)
            return AttachmentLifecycle(
                identity,
                classified.filename,
                classification=classified,
                failure=AttachmentFailure("download", "Discord could not provide this file."),
            )
        if classified.kind != "schematic":
            try:
                url = await self.bot.catbox.upload(classified.filename, data, classified.content_type)
            except SquidError, OSError:
                logger.warning("Could not mirror attachment %s.", classified.filename, exc_info=True)
                return AttachmentLifecycle(
                    identity,
                    classified.filename,
                    classification=classified,
                    failure=AttachmentFailure("mirror", "The media file could not be uploaded."),
                )
            return AttachmentLifecycle(
                identity,
                classified.filename,
                classification=classified,
                media_url=url,
            )

        request = IngestRequest(data=data, filename=classified.filename, uploaded_by_account_id=uploader_account_id)
        if not schematics.available:
            return AttachmentLifecycle(
                identity,
                classified.filename,
                classification=classified,
                request=request,
                failure=AttachmentFailure("analysis", "Schematic analysis is not available right now."),
            )
        try:
            analysis = await schematics.ingest(request)
        except SquidError as error:
            logger.warning("Could not analyze the attached schematic %s.", classified.filename, exc_info=True)
            return AttachmentLifecycle(
                identity,
                classified.filename,
                classification=classified,
                request=request,
                failure=AttachmentFailure("analysis", error.public_detail()),
            )
        return AttachmentLifecycle(
            identity,
            classified.filename,
            classification=classified,
            request=request,
            analysis=analysis,
        )

    async def _record_analyses(
        self, build: Build, attachments: tuple[AttachmentLifecycle, ...]
    ) -> list[AttachmentFailureInfo]:
        """Persist every successful analysis with the explicit primary selection."""
        if build.id is None:
            return []
        schematics = self.bot.services.schematics
        grouped: dict[str, list[AttachmentLifecycle]] = {}
        for attachment in attachments:
            if attachment.request is not None and attachment.analysis is not None:
                grouped.setdefault(attachment.analysis.sha256, []).append(attachment)

        failures: list[AttachmentFailureInfo] = []
        for sha256 in sorted(grouped):
            group = grouped[sha256]
            attachment = next((item for item in group if item.primary), min(group, key=lambda item: item.identity))
            assert attachment.request is not None and attachment.analysis is not None
            try:
                await schematics.record(
                    build.id,
                    attachment.analysis,
                    attachment.request,
                    primary=any(item.primary for item in group),
                )
            except SquidError as error:
                logger.warning(
                    "Could not record the schematic analysis for build %s.",
                    build.id,
                    exc_info=True,
                    extra={
                        "squid.build.id": build.id,
                        "squid.schematic.format": attachment.analysis.analysis.metrics.source_format.value,
                    },
                )
                failures.extend(
                    attachment_failure_for(item, "record", error.public_detail())
                    for item in sorted(group, key=lambda item: item.identity)
                )
            except Exception:
                logger.exception(
                    "Unexpected failure recording schematic analysis for build %s.",
                    build.id,
                    extra={"squid.build.id": build.id, "squid.schematic.sha256": sha256},
                )
                failures.extend(
                    attachment_failure_for(
                        item,
                        "record",
                        "The analyzed schematic could not be attached to the saved submission.",
                    )
                    for item in sorted(group, key=lambda item: item.identity)
                )
        return failures

    async def _note_schematic_duplicates(
        self,
        build: Build,
        attachments: tuple[AttachmentLifecycle, ...],
    ) -> None:
        """Check every usable schematic and retain merged, titled evidence."""
        matches = []
        for attachment in attachments:
            if attachment.analysis is None:
                continue
            try:
                duplicates = await self.bot.services.schematics.find_duplicates(attachment.analysis)
            except SquidError as error:
                logger.warning(
                    "Could not check submitted schematic %s for duplicates.",
                    attachment.filename,
                    exc_info=True,
                    extra={
                        "squid.schematic.format": attachment.analysis.analysis.metrics.source_format.value,
                    },
                )
                self._append_attachment_failures(
                    build,
                    [attachment_failure_for(attachment, "duplicate-check", error.public_detail())],
                )
                continue
            except Exception:
                logger.exception(
                    "Unexpected failure checking submitted schematic %s for duplicates.", attachment.filename
                )
                self._append_attachment_failures(
                    build,
                    [
                        attachment_failure_for(
                            attachment,
                            "duplicate-check",
                            "This schematic could not be checked for possible duplicates.",
                        )
                    ],
                )
                continue
            matches.extend((attachment, candidate) for candidate in duplicates)
        titles: dict[int, str] = {}
        for build_id in {candidate.build_id for _, candidate in matches}:
            candidate_build = await self.builds.get(build_id)
            if candidate_build is not None:
                titles[build_id] = candidate_build.title
        if evidence := merge_duplicate_evidence(matches, titles):
            build.extra_info["schematic_duplicates"] = evidence

    @staticmethod
    def _note_dimension_mismatch(build: Build, attachments: tuple[AttachmentLifecycle, ...]) -> None:
        """Record, but never silently resolve, a disagreement between human and file.

        The declared value wins: a schematic export is frequently cropped to the mechanism and
        legitimately smaller than the build a person measured. Overwriting it would corrupt the
        record, so the discrepancy is surfaced as visible evidence for the reviewers instead.
        """
        primary = primary_schematic(attachments)
        if primary is None or primary.analysis is None:
            return
        measured = primary.analysis.analysis.metrics.dimensions
        declared = (build.width, build.height, build.depth)
        if None in declared or declared == (measured.width, measured.height, measured.length):
            return
        build.extra_info["schematic_dimension_mismatch"] = (
            f"Declared {declared[0]}x{declared[1]}x{declared[2]}, "
            f"schematic measures {measured.width}x{measured.height}x{measured.length}"
        )

    @staticmethod
    def _note_attachment_failures(build: Build | BuildDraft, attachments: tuple[AttachmentLifecycle, ...]) -> None:
        failures = attachment_failure_evidence(attachments)
        if failures:
            BuildSubmitCommands._append_attachment_failures(build, failures)

    @staticmethod
    def _append_attachment_failures(build: Build | BuildDraft, failures: list[AttachmentFailureInfo]) -> None:
        existing = list(build.extra_info.get("attachment_failures", ()))
        build.extra_info["attachment_failures"] = [*existing, *failures]

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

        await self.infer_build_from_message(message)
        return text_node(tr(t"Build recalculated."))
