"""A cog with commands to submit, view, confirm and deny submissions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, TypeVar, cast, TYPE_CHECKING, final, override
import asyncio
import os

import discord
from discord import InteractionResponse, Message, app_commands
from discord.ext import commands
from discord.ext.commands import (
    Context,
    hybrid_group,
    Cog,
    flag,
    FlagConverter,
)
from openai import AsyncOpenAI
from postgrest.base_request_builder import APIResponse, SingleAPIResponse
import vecs

from bot import utils
from bot.submission.parse import parse_dimensions
from bot.submission.ui import BuildSubmissionForm, ConfirmationView
from bot.voting.vote_session import BuildVoteSession
from database import message as msg
from database.builds import get_all_builds, Build
from bot._types import GuildMessageable
from bot.utils import RunningMessage, is_owner_server, check_is_staff, check_is_trusted_or_staff, is_staff
from database.schema import TypeRecord, RestrictionRecord, RestrictionAliasRecord, Status, Category
from database.utils import upload_to_catbox

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

# TODO: Set up a webhook for the bot to handle google form submissions.

_FlagConverter = TypeVar("_FlagConverter", bound=type[FlagConverter])


def fix_converter_annotations(cls: _FlagConverter) -> _FlagConverter:
    """
    Fixes discord.py being unable to evaluate annotations if `from __future__ import annotations` is used AND the `FlagConverter` is a nested class.

    This works because discord.py uses the globals() and locals() function to evaluate annotations at runtime.
    See https://discord.com/channels/336642139381301249/1328967235523317862 for more information about this.
    """
    globals()[cls.__name__] = cls
    return cls


class BuildCog(Cog, name="Build"):
    """A cog with commands to submit, view, confirm and deny submissions."""

    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot
        self.open_vote_sessions: dict[int, BuildVoteSession] = {}
        """A cache of open vote sessions. The key is the message id of the vote message."""

    @hybrid_group(name="build", invoke_without_command=True)
    async def build_hybrid_group(self, ctx: Context):
        """Submit, view, confirm and deny submissions."""
        await ctx.send_help("build")

    @build_hybrid_group.command(name="pending")
    async def get_pending_submissions(self, ctx: Context):
        """Shows an overview of all submitted builds pending review."""
        async with utils.RunningMessage(ctx) as sent_message:
            pending_submissions = await get_all_builds(Status.PENDING)

            if len(pending_submissions) == 0:
                desc = "No open submissions."
            else:
                desc = []
                for sub in pending_submissions:
                    # ID - Title
                    # by Creators - submitted by Submitter
                    desc.append(
                        f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators_ign))}_ - _submitted by {sub.submitter_id}_"
                    )
                desc = "\n\n".join(desc)

            em = utils.info_embed(title="Open Records", description=desc)
            await sent_message.edit(embed=em)

    @build_hybrid_group.command(name="view")
    @app_commands.describe(build_id="The ID of the build you want to see.")
    async def view_build(self, ctx: Context, build_id: int):
        """Displays a submission."""
        async with utils.RunningMessage(ctx) as sent_message:
            submission = await Build.from_id(build_id)

            if submission is None:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                return await sent_message.edit(embed=error_embed)

            await sent_message.edit(content=submission.original_link, embed=await submission.generate_embed())

    @commands.hybrid_command("search")
    async def search_builds(self, ctx: Context, query: str):
        """
        Searches for a build with natural language.

        Args:
            query: The query to search for.
        """
        await ctx.defer()
        client = AsyncOpenAI()
        response = await client.embeddings.create(input=query, model="text-embedding-3-small")
        query_vec = response.data[0].embedding
        vx = vecs.create_client(os.environ["DB_CONNECTION"])
        build_vecs = vx.get_or_create_collection(name="builds", dimension=1536)
        result: list[str] = build_vecs.query(query_vec, limit=1)  # type: ignore
        assert len(result) == 1
        build_id = int(result[0])
        build = await Build.from_id(build_id)
        assert build is not None
        await ctx.send(content=build.original_link, embed=await build.generate_embed())

    @build_hybrid_group.command(name="confirm")
    @app_commands.describe(build_id="The ID of the build you want to confirm.")
    @check_is_staff()
    @commands.check(is_owner_server)
    async def confirm_build(self, ctx: Context, build_id: int):
        """Marks a submission as confirmed.

        This posts the submission to all the servers which configured the bot."""
        async with utils.RunningMessage(ctx) as sent_message:
            build = await Build.from_id(build_id)

            if build is None:
                error_embed = utils.error_embed("Error", "No pending build with that ID.")
                await sent_message.edit(embed=error_embed)
                return

            await build.confirm()
            await self.post_confirmed_build(build)

            success_embed = utils.info_embed("Success", "Submission has been confirmed.")
            await sent_message.edit(embed=success_embed)

    @build_hybrid_group.command(name="deny")
    @app_commands.describe(build_id="The ID of the build you want to deny.")
    @check_is_staff()
    @commands.check(is_owner_server)
    async def deny_build(self, ctx: Context, build_id: int):
        """Marks a submission as denied."""
        async with utils.RunningMessage(ctx) as sent_message:
            build = await Build.from_id(build_id)

            if build is None:
                error_embed = utils.error_embed("Error", "No pending submission with that ID.")
                await sent_message.edit(embed=error_embed)
                return

            await build.deny()

            success_embed = utils.info_embed("Success", "Submission has been denied.")
            await sent_message.edit(embed=success_embed)

    @commands.hybrid_group(name="submit")
    async def submit_group(self, ctx: Context):
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
    async def submit_door(self, ctx: Context, *, flags: SubmitDoorFlags):
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

    @fix_converter_annotations
    class SubmitFormFlags(commands.FlagConverter):
        """Parameters information for the /submit command."""

        first_attachment: discord.Attachment = flag(default=None)
        second_attachment: discord.Attachment = flag(default=None)
        third_attachment: discord.Attachment = flag(default=None)
        fourth_attachment: discord.Attachment = flag(default=None)

    @commands.command(name="submit_form")
    async def submit_form(self, ctx: Context, *, flags: SubmitFormFlags):
        """Submits a build to the database."""
        await ctx.defer()

        build = Build(ai_generated=False)
        for _name, attachment in flags:
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
        followup: discord.Webhook = ctx.interaction.followup  # type: ignore

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

    async def post_confirmed_build(self, build: Build) -> None:
        """Post a confirmed build to the appropriate discord channels.

        Args:
            build (Build): The build to post.
        """
        # TODO: There are no checks to see if the submission has already been posted
        assert build.id is not None
        if build.submission_status != Status.CONFIRMED:
            raise ValueError("The build must be confirmed to post it.")

        em = await build.generate_embed()
        for channel in await build.get_channels_to_post_to(self.bot):
            message = await channel.send(content=build.original_link, embed=em)
            await msg.track_message(message, purpose="view_confirmed_build", build_id=build.id)

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

    @commands.hybrid_group(name="edit")
    @check_is_trusted_or_staff()
    @commands.check(is_owner_server)
    async def edit_group(self, ctx: Context):
        """Edits a record in the database directly."""
        await ctx.send_help("edit")

    @fix_converter_annotations
    class EditDoorFlags(commands.FlagConverter):
        """Parameters information for the `/edit door` command."""

        async def to_build(self) -> Build | None:
            """Convert the flags to a build object, returns None if the build_id is invalid."""
            build = await Build.from_id(self.build_id)
            if build is None:
                return None

            # FIXME: need to distinguish between None and removing the value
            if (works_in := self.works_in) is not None:
                build.version_spec = works_in
            if (build_size := self.build_size) is not None:
                build_dimensions = parse_dimensions(build_size)
                build.width, build.height, build.depth = build_dimensions
            if (door_size := self.door_size) is not None:
                door_dimensions = parse_dimensions(door_size)
                build.door_width, build.door_height, build.door_depth = door_dimensions
            if (pattern := self.pattern) is not None:
                build.door_type = pattern.split(", ")
            if (door_type := self.door_type) is not None:
                build.door_orientation_type = door_type
            if (wp_res := self.wiring_placement_restrictions) is not None:
                build.wiring_placement_restrictions = wp_res.split(", ")
            if (co_res := self.component_restrictions) is not None:
                build.component_restrictions = co_res.split(", ")
            misc_restrictions = [self.locationality, self.directionality]
            build.miscellaneous_restrictions = [x for x in misc_restrictions if x is not None]
            if self.normal_closing_time is not None:
                build.normal_closing_time = self.normal_closing_time
            if self.normal_opening_time is not None:
                build.normal_opening_time = self.normal_opening_time
            if self.information_about_build is not None:
                build.information["user"] = self.information_about_build
            if (ign := self.in_game_name_of_creator) is not None:
                build.creators_ign = ign.split(", ")
            if self.link_to_image is not None:
                build.image_urls = [self.link_to_image]
            if self.link_to_youtube_video is not None:
                build.video_urls = [self.link_to_youtube_video]
            if self.link_to_world_download is not None:
                build.world_download_urls = [self.link_to_world_download]
            if self.server_ip is not None:
                build.server_info["server_ip"] = self.server_ip
            if self.coordinates is not None:
                build.server_info["coordinates"] = self.coordinates
            if self.command_to_get_to_build is not None:
                build.server_info["command_to_build"] = self.command_to_get_to_build
            if self.date_of_creation is not None:
                build.completion_time = self.date_of_creation
            return build

        # fmt: off
        build_id: int = flag(description='The ID of the submission.')
        door_size: str | None = flag(default=None, description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        pattern: str | None = flag(default=None, description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] | None = flag(default=None, description='Door, Skydoor, or Trapdoor.')
        build_size: str | None = flag(default=None, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str | None = flag(default=None, description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".')
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
        server_ip: str | None = flag(default=None, description='The IP of the server where the build is located.')
        coordinates: str | None = flag(default=None, description='The coordinates of the build in the server.')
        command_to_get_to_build: str | None = flag(default=None, description='The command to get to the build in the server.')
        # fmt: on

    @edit_group.command(name="door")
    async def edit_door(self, ctx: Context, *, flags: EditDoorFlags):
        """Edits a door record in the database directly."""
        await ctx.defer()
        async with RunningMessage(ctx) as sent_message:
            build = await flags.to_build()
            if build is None:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                return await sent_message.edit(embed=error_embed)

            preview_embed = await build.generate_embed()

            # Show a preview of the changes and ask for confirmation
            await sent_message.edit(embed=utils.info_embed("Waiting", "User confirming changes..."))
            if ctx.interaction:
                view = ConfirmationView()
                preview = await ctx.interaction.followup.send(
                    "Here is a preview of the changes. Use the buttons to confirm or cancel.",
                    embed=preview_embed,
                    view=view,
                    ephemeral=True,
                    wait=True,
                )
                await view.wait()
                await preview.delete()
                if view.value is None:
                    await sent_message.edit(
                        embed=utils.info_embed("Timed out", "Build edit canceled due to inactivity.")
                    )
                elif view.value:
                    await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
                    await build.save()
                    await build.update_messages(self.bot)
                    await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))
                else:
                    await sent_message.edit(embed=utils.info_embed("Cancelled", "Build edit canceled by user"))
            else:  # Not an interaction, so we can't use buttons for confirmation
                await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
                await build.save()
                await build.update_messages(self.bot)
                await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))

    @commands.hybrid_command()
    async def list_patterns(self, ctx: Context):
        """Lists all the available patterns."""
        async with RunningMessage(ctx) as sent_message:
            patterns: APIResponse[TypeRecord] = await self.bot.db.table("types").select("*").execute()
            names = [pattern["name"] for pattern in patterns.data]
            await sent_message.edit(
                content="Here are the available patterns:", embed=utils.info_embed("Patterns", ", ".join(names))
            )

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

    @build_hybrid_group.command("recalc")
    @check_is_trusted_or_staff()
    @commands.check(is_owner_server)
    async def recalc(self, ctx: Context, message: discord.Message):
        """Recalculate a build from a message."""
        await ctx.defer(ephemeral=True)
        await self.infer_build_from_message(message)
        await ctx.send("Build recalculated.", ephemeral=True)

    @commands.command("add_alias")
    @check_is_staff()
    @commands.check(is_owner_server)
    async def add_restriction_alias(self, ctx: Context, restriction_id: int, alias: str):
        """Add an alias for a restriction."""
        async with RunningMessage(ctx) as sent_message:
            await (
                self.bot.db.table("restriction_aliases")
                .insert({"restriction_id": restriction_id, "alias": alias})
                .execute()
            )
            await sent_message.edit(embed=utils.info_embed("Success", "Alias added."))

    @commands.command("search_restrictions")
    @check_is_staff()
    @commands.check(is_owner_server)
    async def search_restrictions(self, ctx: Context, query: str | None):
        """This runs a substring search on the restriction names."""
        async with RunningMessage(ctx) as sent_message:
            if query:
                response: APIResponse[RestrictionRecord] = (
                    await self.bot.db.table("restrictions").select("*").ilike("name", f"%{query}%").execute()
                )
                response_alias: APIResponse[RestrictionAliasRecord] = (
                    await self.bot.db.table("restriction_aliases").select("*").ilike("alias", f"%{query}%").execute()
                )
            else:
                response = await self.bot.db.table("restrictions").select("*").execute()
                response_alias = await self.bot.db.table("restriction_aliases").select("*").execute()
            restrictions = response.data
            aliases = response_alias.data

            description = "\n".join([f"{restriction['id']}: {restriction['name']}" for restriction in restrictions])
            description += "\n"
            description += "\n".join([f"{alias['restriction_id']}: {alias['alias']} (alias)" for alias in aliases])
            await sent_message.edit(embed=utils.info_embed("Restrictions", description))


async def setup(bot: "RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(BuildCog(bot))
