"""A cog with commands to submit builds."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

import discord
from discord import Message, app_commands
from discord.ext import commands
from discord.ext.commands import (
    Cog,
    Context,
    flag,
)

from squid.bot import utils
from squid.bot._types import GuildMessageable
from squid.bot.converter import DimensionsConverter, ListConverter
from squid.bot.submission.ui.components import DynamicBuildEditButton
from squid.bot.submission.ui.views import BuildSubmissionForm
from squid.bot.utils import RunningMessage, check_is_owner_server, check_is_trusted_or_staff, fix_converter_annotations
from squid.db.builds import Build
from squid.db.schema import Category, Status
from squid.db.utils import upload_to_catbox

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid

# TODO: Set up a webhook for the bot to handle google form submissions.


class BuildSubmitCog[BotT: RedstoneSquid](Cog, name="Build"):
    """A cog with commands to submit builds."""

    def __init__(self, bot: BotT):
        self.bot = bot

    @commands.hybrid_group(name="submit")
    async def submit_group(self, ctx: Context[BotT]):
        """Submit a build to the database."""
        await ctx.send_help("submit")

    @fix_converter_annotations
    class SubmitDoorFlags(commands.FlagConverter):
        """Parameters information for the /submit door command."""

        def to_build(self) -> Build:
            """Convert the flags to a build object."""
            build = Build()
            build.record_category = self.record_category
            build.version_spec = self.works_in
            build.width, build.height, build.depth = self.build_size
            build.door_width, build.door_height, build.door_depth = self.door_size
            build.door_type = self.pattern
            build.door_orientation_type = self.door_type
            build.wiring_placement_restrictions = self.wiring_placement_restrictions
            build.component_restrictions = self.component_restrictions

            if (locationality := self.locationality) is not None and locationality != "Not locational":
                build.miscellaneous_restrictions.append(locationality)
            if (directionality := self.directionality) is not None and directionality != "Not directional":
                build.miscellaneous_restrictions.append(directionality)

            build.normal_closing_time = self.normal_closing_time
            build.normal_opening_time = self.normal_opening_time

            if self.information_about_build is not None:
                build.extra_info["user"] = self.information_about_build
            build.creators_ign = self.creators
            build.image_urls = self.image_urls
            build.video_urls = self.video_urls
            build.world_download_urls = self.world_download_urls
            build.completion_time = self.date_of_creation
            return build

        _list_default = lambda ctx: []  # noqa: E731

        # fmt: off
        # Intentionally moved closer to the submit command
        door_size: tuple[int | None, int | None, int | None] = flag(converter=DimensionsConverter, description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        record_category: Literal['Smallest', 'Fastest', 'First'] | None = flag(default=None, description='Is this build a record?')
        pattern: list[str] = flag(default=lambda ctx: ['Regular'], converter=ListConverter, description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default='Door', description='Door, Skydoor, or Trapdoor.')
        build_size: tuple[int | None, int | None, int | None] = flag(default=lambda ctx: (None, None, None), converter=DimensionsConverter, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str | None = flag(default=None, description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".')
        # TODO: merge all restrictions into one field and use build.set_restrictions
        wiring_placement_restrictions: list[str] = flag(default=_list_default, converter=ListConverter, description='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.')
        component_restrictions: list[str] = flag(default=_list_default, converter=ListConverter, description='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.')
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
                build = flags.to_build()
                build.submitter_id = ctx.author.id
                build.ai_generated = False
                build.category = Category.DOOR
                build.submission_status = Status.PENDING

                build_handler = self.bot.for_build(build)
                await asyncio.gather(
                    build.save(),
                    followup.send(
                        "Here is a preview of the submission. Use /edit if you have made a mistake",
                        embed=await build_handler.generate_embed(),
                        ephemeral=True,
                    ),
                )

                success_embed = utils.info_embed(
                    "Success",
                    f"Build submitted successfully!\nThe build ID is: {build.id}",
                )
                await asyncio.gather(
                    message.edit(embed=success_embed),
                    build_handler.post_for_voting(),
                )
        else:
            raise NotImplementedError("This command is only available as a slash command for now.")

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

        async def _handle_attachment(attachment: discord.Attachment | None):
            if attachment is None:
                return
            assert attachment.content_type is not None
            if not attachment.content_type.startswith("image") and not attachment.content_type.startswith("video"):
                raise ValueError(f"Unsupported content type: {attachment.content_type}")

            url = await upload_to_catbox(attachment.filename, await attachment.read(), attachment.content_type)
            if attachment.content_type.startswith("image"):
                build.image_urls.append(url)
            elif attachment.content_type.startswith("video"):
                build.video_urls.append(url)

        await asyncio.gather(*(_handle_attachment(attachment) for attachment in attachments))

        view = BuildSubmissionForm(build)
        followup = interaction.followup

        await followup.send("Use the select menus then click the button", view=view)
        await view.wait()
        if view.value is None:
            await followup.send("Submission canceled due to inactivity", ephemeral=True)
            return
        elif view.value is False:
            await followup.send("Submission canceled by user", ephemeral=True)
            return
        else:
            await build.save()
            await asyncio.gather(
                followup.send(
                    "Here is a preview of the submission. Use /edit if you have made a mistake",
                    embed=await self.bot.for_build(build).generate_embed(),
                    ephemeral=True,
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
            raise ValueError("The build must be confirmed to post it.")

        build_handler = self.bot.for_build(build)
        em = await build_handler.generate_embed()

        async def _send_msg(channel: GuildMessageable):
            message = await channel.send(content=build.original_link, embed=em)
            await self.bot.db.message.track_message(message, purpose="view_confirmed_build", build_id=build.id)

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

        build = await Build.ai_generate_from_message(message, model="deepseek/deepseek-chat")
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

        build.submission_status = Status.PENDING
        build.category = Category.DOOR
        build.submitter_id = message.author.id
        # Order is important here.
        await build.save()
        await self.bot.for_build(build).post_for_voting(type="add")

    @commands.hybrid_command("recalc")
    @check_is_trusted_or_staff()
    @check_is_owner_server()
    async def recalc(self, ctx: Context[BotT], message: discord.Message):
        """Recalculate a build from a message."""
        await ctx.defer(ephemeral=True)
        await self.infer_build_from_message(message)
        await ctx.send("Build recalculated.", ephemeral=True)


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(BuildSubmitCog(bot))
