"""A cog with commands to submit builds."""

from __future__ import annotations

from typing import Literal, cast, TYPE_CHECKING
import asyncio

import discord
from discord import InteractionResponse, Message, app_commands
from discord.ext import commands
from discord.ext.commands import (
    Context,
    Cog,
    flag,
)

from bot import utils
from bot.submission.parse import parse_dimensions
from bot.submission.ui import BuildSubmissionForm, DynamicBuildEditButton
from bot.voting.vote_session import BuildVoteSession
from database.builds import Build
from bot.utils import RunningMessage, fix_converter_annotations, check_is_owner_server, check_is_trusted_or_staff
from database.schema import Status, Category
from database.utils import upload_to_catbox

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

# TODO: Set up a webhook for the bot to handle google form submissions.


class BuildSubmitCog[BotT: RedstoneSquid](Cog, name="Build"):
    """A cog with commands to submit builds."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.open_vote_sessions: dict[int, BuildVoteSession] = {}
        """A cache of open vote sessions. The key is the message id of the vote message."""

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

            if (build_size := self.build_size) is not None:
                build_dimensions = parse_dimensions(build_size)
                build.width, build.height, build.depth = build_dimensions

            door_dimensions = parse_dimensions(self.door_size)
            build.door_width, build.door_height, build.door_depth = door_dimensions

            build.door_type = self.pattern.split(", ")
            build.door_orientation_type = self.door_type

            if (wp_res := self.wiring_placement_restrictions) is not None:
                build.wiring_placement_restrictions = wp_res.split(", ")
            else:
                build.wiring_placement_restrictions = []

            if (co_res := self.component_restrictions) is not None:
                build.component_restrictions = co_res.split(", ")
            else:
                build.component_restrictions = []
            misc_restrictions = [self.locationality, self.directionality]
            build.miscellaneous_restrictions = [x for x in misc_restrictions if x is not None]

            build.normal_closing_time = self.normal_closing_time
            build.normal_opening_time = self.normal_opening_time

            if self.information_about_build is not None:
                build.information["user"] = self.information_about_build
            if (ign := self.in_game_name_of_creator) is not None:
                build.creators_ign = ign.split(", ")
            else:
                build.creators_ign = []

            build.image_urls = [self.link_to_image] if self.link_to_image is not None else []
            build.video_urls = [self.link_to_youtube_video] if self.link_to_youtube_video is not None else []
            build.world_download_urls = [self.link_to_world_download] if self.link_to_world_download is not None else []
            build.completion_time = self.date_of_creation
            return build

        # fmt: off
        # Intentionally moved closer to the submit command
        door_size: str = flag(description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        record_category: Literal['Smallest', 'Fastest', 'First'] = flag(default=None, description='Is this build a record?')
        pattern: str = flag(default='Regular', description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default='Door', description='Door, Skydoor, or Trapdoor.')
        build_size: str | None = flag(default=None, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str | None = flag(default=None, description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".')
        # TODO: merge all restrictions into one field and use build.set_restrictions
        wiring_placement_restrictions: str | None = flag(default=None, description='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.')
        component_restrictions: str | None = flag(default=None, description='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.')
        information_about_build: str | None = flag(default=None, description='Any additional information about the build.')
        normal_closing_time: int | None = flag(default=None, description='The time it takes to close the door, in gameticks. (1s = 20gt)')
        normal_opening_time: int | None = flag(default=None, description='The time it takes to open the door, in gameticks. (1s = 20gt)')
        date_of_creation: str | None = flag(default=None, description='The date the build was created.')
        in_game_name_of_creator: str | None = flag(default=None, description='The in-game name of the creator(s).')
        locationality: Literal["Locational", "Locational with fixes"] | None = flag(default=None, description='Whether the build works everywhere, or only in certain locations.')
        directionality: Literal["Directional", "Directional with fixes"] | None = flag(default=None, description='Whether the build works in all directions, or only in certain directions.')
        link_to_image: str | None = flag(default=None, description='A link to an image of the build. Use direct links only. e.g."https://i.imgur.com/abc123.png"')
        link_to_youtube_video: str | None = flag(default=None, description='A link to a video of the build.')
        link_to_world_download: str | None = flag(default=None, description='A link to download the world.')
        # fmt: on

    @submit_group.command(name="door")
    async def submit_door(self, ctx: Context[BotT], *, flags: SubmitDoorFlags):
        """Submits a record to the database directly."""
        # TODO: Discord only allows 25 options. Split this into multiple commands.
        interaction = cast(discord.Interaction, ctx.interaction)
        response: InteractionResponse = interaction.response  # type: ignore
        await response.defer()

        followup: discord.Webhook = interaction.followup  # type: ignore

        async with RunningMessage(followup) as message:
            build = flags.to_build()
            build.submitter_id = ctx.author.id
            build.ai_generated = False
            build.category = Category.DOOR
            build.submission_status = Status.PENDING

            await build.save()
            # Shows the submission to the user
            await followup.send(
                "Here is a preview of the submission. Use /edit if you have made a mistake",
                embed=await build.generate_embed(),
                ephemeral=True,
            )

            success_embed = utils.info_embed(
                "Success",
                f"Build submitted successfully!\nThe build ID is: {build.id}",
            )
            await message.edit(embed=success_embed)
            await self.post_build_for_voting(build)

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
        for attachment in attachments:
            if attachment is None:
                continue

            assert isinstance(attachment, discord.Attachment)
            assert attachment.content_type is not None
            if not attachment.content_type.startswith("image") and not attachment.content_type.startswith("video"):
                raise ValueError(f"Unsupported content type: {attachment.content_type}")

            url = await upload_to_catbox(attachment.filename, await attachment.read(), attachment.content_type)
            if attachment.content_type.startswith("image"):
                build.image_urls.append(url)
            elif attachment.content_type.startswith("video"):
                build.video_urls.append(url)

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
            await followup.send(
                "Here is a preview of the submission. Use /edit if you have made a mistake",
                embed=await build.generate_embed(),
                ephemeral=True,
            )
            await self.post_build_for_voting(build)

    @commands.Cog.listener("on_build_confirmed")
    async def post_confirmed_build(self, build: Build) -> None:
        """Post a confirmed build to the appropriate discord channels.

        Args:
            build (Build): The build to post.
        """
        assert build.id is not None
        if build.submission_status != Status.CONFIRMED:
            raise ValueError("The build must be confirmed to post it.")

        em = await build.generate_embed()
        for channel in await build.get_channels_to_post_to(self.bot):
            message = await channel.send(content=build.original_link, embed=em)
            await self.bot.db.message.track_message(message, purpose="view_confirmed_build", build_id=build.id)

    async def post_build_for_voting(self, build: Build, type: Literal["add", "update"] = "add") -> None:
        """
        Post a build for voting.

        Args:
            build (Build): The build to post.
            type (Literal["add", "update"]): Whether to add or update the build.
        """
        if type == "update":
            raise NotImplementedError("Updating builds is not yet implemented.")

        if build.submission_status != Status.PENDING:
            raise ValueError("The build must be pending to post it.")

        em = await build.generate_embed()
        tasks: list[asyncio.Task[discord.Message]] = []
        for vote_channel in await build.get_channels_to_post_to(self.bot):
            tasks.append(asyncio.create_task(vote_channel.send(content=build.original_link, embed=em)))
        messages = await asyncio.gather(*tasks)

        assert build.submitter_id is not None
        session = await BuildVoteSession.create(self.bot, messages, build.submitter_id, build, type)
        for message in messages:
            self.open_vote_sessions[message.id] = session

    @Cog.listener(name="on_message")
    async def infer_build_from_message(self, message: Message):
        """Infer a build from a message."""
        if message.author.bot:
            return

        build_logs = 726156829629087814
        record_logs = 667401499554611210

        if message.channel.id not in [build_logs, record_logs]:
            return

        build = await Build.ai_generate_from_message(message)
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
        await self.post_build_for_voting(build, type="add")

    @commands.hybrid_command("recalc")
    @check_is_trusted_or_staff()
    @commands.check(check_is_owner_server)
    async def recalc(self, ctx: Context[BotT], message: discord.Message):
        """Recalculate a build from a message."""
        await ctx.defer(ephemeral=True)
        await self.infer_build_from_message(message)
        await ctx.send("Build recalculated.", ephemeral=True)


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(BuildSubmitCog(bot))
