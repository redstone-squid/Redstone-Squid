"""A cog with commands to submit builds."""

import asyncio
from typing import TYPE_CHECKING, Literal

import discord
from discord import Message, app_commands
from discord.ext import commands, tasks
from discord.ext.commands import (
    Cog,
    Context,
    flag,
)

from squid.bot._types import GuildMessageable
from squid.bot.message_adapter import to_tracked_message
from squid.bot.submission.ui.components import DynamicBuildEditButton
from squid.bot.submission.ui.views import BuildSubmissionForm
from squid.bot.utils.components import StaticLayout, edit_layout, info_layout, no_mentions
from squid.bot.utils.converters import DimensionsConverter, ListConverter, fix_converter_annotations
from squid.bot.utils.embeds import RunningMessage
from squid.bot.utils.permissions import check_is_owner_server, check_is_trusted_or_staff
from squid.bot.utils.uploads import upload_to_catbox
from squid.builds.application import BuildInferenceInput, DoorSubmissionInput
from squid.builds.domain import Build, Status

if TYPE_CHECKING:
    import squid.bot.app

# TODO: Set up a webhook for the bot to handle google form submissions.


class BuildSubmitCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="Build"):
    """A cog with commands to submit builds."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.builds = bot.services.builds
        self.inference = bot.services.build_inference
        self.messages = bot.services.messages
        self.update_record_titles.start()

    @commands.hybrid_group(name="submit")
    async def submit_group(self, ctx: Context[BotT]):
        """Submit a build to the database."""
        await ctx.send_help("submit")

    @fix_converter_annotations
    class SubmitDoorFlags(commands.FlagConverter):
        """Parameters information for the /submit door command."""

        def to_submission(self, submitter_id: int) -> DoorSubmissionInput:
            """Convert Discord flags to framework-neutral submission input."""
            return DoorSubmissionInput(
                submitter_id=submitter_id,
                door_size=self.door_size,
                record_category=self.record_category,
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
        record_category: Literal['Smallest', 'Fastest', 'First'] | None = flag(default=None, description='Is this build a record?')
        pattern: list[str] = flag(default=lambda ctx: ['Regular'], converter=ListConverter, description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default='Door', description='Door, Skydoor, or Trapdoor.')
        build_size: tuple[int | None, int | None, int | None] = flag(default=lambda ctx: (None, None, None), converter=DimensionsConverter, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str | None = flag(default=None, description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".')
        restrictions: list[str] = flag(default=_list_default, converter=ListConverter, description='For example, "Seamless, Full Flush, No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.')
        information_about_build: str | None = flag(default=None, description='Any additional information about the build.')
        normal_closing_time: int | None = flag(default=None, description='The time it takes to close the door, in gameticks. (1s = 20gt)')
        normal_opening_time: int | None = flag(default=None, description='The time it takes to open the door, in gameticks. (1s = 20gt)')
        date_of_creation: str | None = flag(default=None, description='The date the build was created.')
        creators: list[str] = flag(default=_list_default, converter=ListConverter, description='The in-game name of the creator(s).')
        locationality: Literal["Locational", "Locational with fixes", "Not locational"] | None = flag(default=None, description='Whether the build works everywhere, or only in certain locations.')
        directionality: Literal["Directional", "Directional with fixes", "Not directional"] | None = flag(default=None, description='Whether the build works in all directions, or only in certain directions.')
        image_urls: list[str] = flag(name="image_links", default=_list_default, converter=ListConverter, description='Links to images of the build.')
        video_urls: list[str] = flag(name="video_links", default=_list_default, converter=ListConverter, description='Links to videos of the build.')
        world_download_urls: list[str] = flag(name="world_download_links", default=_list_default, converter=ListConverter, description='Links to download the world.')
        # fmt: on

    @submit_group.command(name="door")
    async def submit_door(self, ctx: Context[BotT], *, flags: SubmitDoorFlags):
        """Submits a record to the database directly."""
        # TODO: Discord only allows 25 options. Split this into multiple commands.
        if ctx.interaction:
            interaction = ctx.interaction
            await interaction.response.defer()
            followup = interaction.followup

            async with RunningMessage(followup) as message:
                build = await self.builds.submit_door(flags.to_submission(ctx.author.id))
                build_handler = self.bot.for_build(build)
                await followup.send(
                    view=StaticLayout(
                        discord.ui.TextDisplay(
                            "Here is a preview of the submission. Use `/edit` if you have made a mistake."
                        ),
                        await build_handler.render_container(),
                    ),
                    ephemeral=True,
                    allowed_mentions=no_mentions(),
                )

                success_layout = info_layout(
                    "Success",
                    f"Build submitted successfully!\nThe build ID is: {build.id}",
                )
                await asyncio.gather(
                    edit_layout(message, success_layout, allowed_mentions=no_mentions()),
                    build_handler.post_for_voting(),
                )
        else:
            msg = "This command is only available as a slash command for now."
            raise NotImplementedError(msg)

    @app_commands.command(name="submit_form")
    async def submit_form(
        self,
        interaction: discord.Interaction[BotT],
        *,
        first_attachment: discord.Attachment | None = None,
        second_attachment: discord.Attachment | None = None,
        third_attachment: discord.Attachment | None = None,
        fourth_attachment: discord.Attachment | None = None,
    ):
        """Submits a build to the database."""
        await interaction.response.defer()

        build = Build(ai_generated=False)
        attachments = [first_attachment, second_attachment, third_attachment, fourth_attachment]

        async def _handle_attachment(attachment: discord.Attachment | None) -> tuple[str, str] | None:
            if attachment is None:
                return None
            assert attachment.content_type is not None
            if not attachment.content_type.startswith("image") and not attachment.content_type.startswith("video"):
                msg = f"Unsupported content type: {attachment.content_type}"
                raise ValueError(msg)

            url = await upload_to_catbox(attachment.filename, await attachment.read(), attachment.content_type)
            if attachment.content_type.startswith("image"):
                return "image", url
            return "video", url

        uploaded_media = await asyncio.gather(*(_handle_attachment(attachment) for attachment in attachments))
        for uploaded in uploaded_media:
            if uploaded is None:
                continue
            kind, url = uploaded
            if kind == "image":
                build.image_urls.append(url)
            else:
                build.video_urls.append(url)

        view = BuildSubmissionForm(build, self.builds)
        followup = interaction.followup

        await followup.send(view=view, allowed_mentions=no_mentions())
        await view.wait()
        if view.value is None:
            await followup.send("Submission canceled due to inactivity", ephemeral=True)
            return
        if view.value is False:
            await followup.send("Submission canceled by user", ephemeral=True)
            return
        await self.builds.submit(build, submitter_id=interaction.user.id, ai_generated=False)
        await asyncio.gather(
            followup.send(
                view=StaticLayout(
                    discord.ui.TextDisplay(
                        "Here is a preview of the submission. Use `/edit` if you have made a mistake."
                    ),
                    await self.bot.for_build(build).render_container(),
                ),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            ),
            self.bot.for_build(build).post_for_voting(),
        )

    @commands.Cog.listener("on_build_confirmed")
    async def post_confirmed_build(self, build: Build) -> None:
        """Post a confirmed build to the appropriate discord channels.

        Args:
            build (Build): The build to post.
        """
        assert build.id is not None
        if build.submission_status != Status.CONFIRMED:
            msg = "The build must be confirmed to post it."
            raise ValueError(msg)

        build_handler = self.bot.for_build(build)
        layout = await build_handler.render_layout()

        async def _send_msg(channel: GuildMessageable):
            message = await channel.send(view=layout, allowed_mentions=no_mentions())
            await self.messages.track(
                to_tracked_message(message),
                purpose="view_confirmed_build",
                build_id=build.id,
            )

        await asyncio.gather(*(_send_msg(channel) for channel in await build_handler.get_channels_to_post_to()))

    @Cog.listener(name="on_message")
    async def infer_build_from_message(self, message: Message):
        """Infer a build from a message."""
        if message.author.bot:
            return

        build_logs = 726156829629087814
        record_logs = 667401499554611210

        if message.channel.id not in [build_logs, record_logs]:
            return

        build = await self.inference.infer(
            BuildInferenceInput(
                author_name=message.author.display_name,
                content=message.clean_content,
                message_id=message.id,
                author_id=message.author.id,
                channel_id=message.channel.id,
                server_id=message.guild.id if message.guild is not None else None,
            ),
            model="deepseek/deepseek-v3.2",
        )
        if build is None:
            return

        for attachment in message.attachments:
            if attachment.content_type is None:
                continue
            url = await upload_to_catbox(attachment.filename, await attachment.read(), attachment.content_type)
            if attachment.content_type.startswith("image"):
                build.image_urls.append(url)
            elif attachment.content_type.startswith("video"):
                build.video_urls.append(url)

        # Order is important here.
        await self.builds.submit(build, submitter_id=message.author.id, ai_generated=True)
        await self.bot.for_build(build).post_for_voting(type="add")

    @commands.hybrid_command("recalc")
    @check_is_trusted_or_staff()
    @check_is_owner_server()
    async def recalc(self, ctx: Context[BotT], message: discord.Message):
        """Recalculate a build from a message."""
        await ctx.defer(ephemeral=True)
        await self.infer_build_from_message(message)
        await ctx.send("Build recalculated.", ephemeral=True)

    @tasks.loop(minutes=5.0)
    async def update_record_titles(self):
        await self.builds.refresh_record_titles()


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(BuildSubmitCog(bot))
