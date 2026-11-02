"""A cog with commands to submit builds."""

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

import discord
from discord import Message
from discord.ext import commands
from discord.ext.commands import (
    Cog,
    Context,
    flag,
)

from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.attachments import AttachmentKind, classify_attachment
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.ingestion import ingest_message_bundle
from squid.bot.submission.media import CatboxMirror
from squid.bot.submission.ui.components import EphemeralBuildEditButton
from squid.bot.submission.ui.views import BuildSubmissionForm
from squid.bot.utils.autocomplete import autocompletes, suggests
from squid.bot.utils.components import StaticLayout, edit_layout, info_layout, no_mentions, text_layout
from squid.bot.utils.converters import DimensionsConverter, ListConverter, fix_converter_annotations
from squid.bot.utils.embeds import RunningMessage
from squid.bot.utils.permissions import requires
from squid.builds.application import (
    BuildInferenceService,
    BuildService,
    DoorSubmissionInput,
)
from squid.builds.domain import Build, BuildDraft
from squid.core.errors import SquidError
from squid.core.i18n import _
from squid.messages.application import MessageService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_RECALC
from squid.schematics.application import IngestedSchematic, IngestRequest

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

# TODO: Set up a webhook for the bot to handle google form submissions.


class BuildSubmitCommands[BotT: "squid.bot.app.RedstoneSquid"](BuildCommandGroup[BotT]):
    """A cog with commands to submit builds."""

    bot: BotT
    builds: BuildService
    inference: BuildInferenceService
    messages: MessageService

    @fix_converter_annotations
    class SubmitDoorFlags(commands.FlagConverter):
        """Parameters for the `/build submit-full` command."""

        def to_submission(self, submitter_id: int) -> DoorSubmissionInput:
            """Convert Discord flags to framework-neutral submission input."""
            return DoorSubmissionInput(
                submitter_id=submitter_id,
                door_size=self.door_size,
                pattern=tuple(self.pattern),
                door_type=self.door_type,
                build_size=self.build_size,
                works_in=self.works_in,
                restrictions=tuple(self.restrictions),
                information_about_build=self.information_about_build,
                normal_closing_time=self.normal_closing_time,
                normal_opening_time=self.normal_opening_time,
                date_of_creation=self.date_of_creation,
                creators=tuple(self.creators),
                locationality=self.locationality,
                directionality=self.directionality,
                image_urls=tuple(self.image_urls),
                video_urls=tuple(self.video_urls),
                world_download_urls=tuple(self.world_download_urls),
            )

        _list_default = lambda ctx: []  # type: ignore

        # fmt: off
        # Intentionally moved closer to the submit command
        door_size: tuple[int | None, int | None, int | None] = flag(converter=DimensionsConverter, description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        pattern: list[str] = flag(default=lambda ctx: ['Regular'], converter=ListConverter, description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default='Door', description='Door, Skydoor, or Trapdoor.')
        build_size: tuple[int | None, int | None, int | None] = flag(default=lambda ctx: (None, None, None), converter=DimensionsConverter, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str | None = flag(default=None, description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".')
        restrictions: list[str] = flag(default=_list_default, converter=ListConverter, description='For example, "Seamless, Full Flush, No Pistons, No Slime Blocks". See `/info docs` for the complete list.')
        information_about_build: str | None = flag(default=None, description='Any additional information about the build.')
        normal_closing_time: int | None = flag(default=None, description='The time it takes to close the door, in game ticks (20 per second).')
        normal_opening_time: int | None = flag(default=None, description='The time it takes to open the door, in game ticks (20 per second).')
        date_of_creation: str | None = flag(default=None, description='The date the build was created.')
        creators: list[str] = flag(default=_list_default, converter=ListConverter, description='The in-game name of the creator(s).')
        locationality: Literal["Locational", "Locational with fixes", "Not locational"] | None = flag(default=None, description='Whether the build works everywhere, or only in certain locations.')
        directionality: Literal["Directional", "Directional with fixes", "Not directional"] | None = flag(default=None, description='Whether the build works in all directions, or only in certain directions.')
        image_urls: list[str] = flag(name="image_links", default=_list_default, converter=ListConverter, description='Links to images of the build.')
        video_urls: list[str] = flag(name="video_links", default=_list_default, converter=ListConverter, description='Links to videos of the build.')
        world_download_urls: list[str] = flag(name="world_download_links", default=_list_default, converter=ListConverter, description='Links to download the world.')
        # fmt: on

    @autocompletes(
        pattern=suggests("approved_patterns", multi=True),
        works_in="approved_source_versions",
        restrictions=suggests("approved_restrictions", multi=True),
        creators=suggests("creators", multi=True),
    )
    @BuildCommandGroup.build_hybrid_group.command(name="submit-full")  # type: ignore
    async def submit_door(self, ctx: Context[BotT], *, flags: SubmitDoorFlags):
        """Submit a build with every field available at once.

        Prefer `/build submit` unless you need to set every field at once.
        """
        # TODO: Discord only allows 25 options. Split this into multiple commands.
        if ctx.interaction:
            interaction = ctx.interaction
            await interaction.response.defer()
            followup = interaction.followup
            locale = await resolve_locale(ctx, self.bot.services.settings)

            async with RunningMessage(followup, locale=locale) as message:
                build = await self.builds.submit_door(flags.to_submission(ctx.author.id))
                build_handler = self.bot.for_build(build)
                await followup.send(
                    view=StaticLayout(
                        discord.ui.TextDisplay(
                            t(
                                locale,
                                _("Here is a preview of the submission. Use `/build edit` if you have made a mistake."),
                            )
                        ),
                        await build_handler.render_container(),
                    ),
                    ephemeral=True,
                    allowed_mentions=no_mentions(),
                )

                success_layout = info_layout(
                    t(locale, _("Success")),
                    t(locale, _("Build submitted successfully!\nThe build ID is: {id}"), id=build.id),
                )
                await asyncio.gather(
                    edit_layout(message, success_layout, allowed_mentions=no_mentions()),
                    build_handler.post_for_voting(),
                )
        else:
            locale = await resolve_locale(ctx, self.bot.services.settings)
            msg = t(locale, _("This command is only available as a slash command for now."))
            raise NotImplementedError(msg)

    @BuildCommandGroup.build_hybrid_group.app_command.command(name="submit")  # type: ignore
    async def submit_form(
        self,
        interaction: discord.Interaction[BotT],
        *,
        first_attachment: discord.Attachment | None = None,
        second_attachment: discord.Attachment | None = None,
        third_attachment: discord.Attachment | None = None,
        fourth_attachment: discord.Attachment | None = None,
    ):
        """Submit a build with a guided form and optional attachments.

        Prefer this unless you need to set every field at once — see `/build submit-full`.
        """
        await interaction.response.defer(ephemeral=True)
        locale = await resolve_locale(interaction, self.bot.services.settings)

        draft = BuildDraft(ai_generated=False)
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

        # Prefilling is safe here: `draft` was constructed empty a few lines above, so there is
        # no human-declared value to overwrite. The modal shows these as editable defaults, and
        # whatever the human submits wins from that point on.
        analyses = await self._analyse_attachments(pending_schematics, uploader_id=interaction.user.id)
        if analyses:
            measured = analyses[0][1].analysis.metrics.dimensions
            draft.dimensions = (measured.width, measured.height, measured.length)

        view = BuildSubmissionForm(
            draft,
            self.builds,
            author_id=interaction.user.id,
            locale=locale,
        )
        workspace_message = await interaction.followup.send(  # pyrefly: ignore[no-matching-overload]
            view=view,
            ephemeral=True,
            wait=True,
            allowed_mentions=no_mentions(),
        )
        await view.wait()
        if view.value is None:
            await edit_layout(
                workspace_message,
                text_layout(t(locale, _("Submission expired. Nothing was saved."))),
                allowed_mentions=no_mentions(),
            )
            return
        if view.value is False:
            await edit_layout(
                workspace_message,
                text_layout(t(locale, _("Submission cancelled. Nothing was saved."))),
                allowed_mentions=no_mentions(),
            )
            return

        build = draft.finalize()
        self._note_dimension_mismatch(build, analyses)
        await self._note_schematic_duplicates(build, analyses)
        await self.builds.submit(build, submitter_id=interaction.user.id, ai_generated=False)
        await self._record_analyses(build, analyses, uploader_id=interaction.user.id)

        preview = StaticLayout(
            discord.ui.TextDisplay(
                t(
                    locale,
                    _("## Submitted for review\nSubmission ID: `{id}`\nStaff can now review and vote on this build."),
                    id=build.id,
                )
            ),
            await self.bot.for_build(build).render_container(),
            discord.ui.ActionRow(EphemeralBuildEditButton(build)),
        )
        await asyncio.gather(
            edit_layout(workspace_message, preview, allowed_mentions=no_mentions()),
            self.bot.for_build(build).post_for_voting(),
        )

    async def _analyse_attachments(
        self, pending: Sequence[tuple[str, bytes]], *, uploader_id: int
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
            request = IngestRequest(data=data, filename=filename, uploaded_by_discord_id=uploader_id)
            try:
                analysed.append((request, await schematics.ingest(request)))
            except SquidError:
                logger.warning("Could not analyze the attached schematic %s.", filename, exc_info=True)
        return analysed

    async def _record_analyses(
        self, build: Build, analyses: Sequence[tuple[IngestRequest, IngestedSchematic]], *, uploader_id: int
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

    @Cog.listener(name="on_message")
    async def infer_build_from_message(self, message: Message):
        """Infer a build from a message."""
        if message.author.bot:
            return

        if message.channel.id not in self.bot.community_config.build_log_channel_ids:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
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

    @BuildCommandGroup.build_hybrid_group.command(name="recalc")  # type: ignore
    @requires(BUILD_SUBMISSION_RECALC)
    async def recalc(self, ctx: Context[BotT], message: discord.Message):
        """Recalculate a build from a message."""
        await ctx.defer(ephemeral=True)
        await self.infer_build_from_message(message)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=text_layout(t(locale, _("Build recalculated."))),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
