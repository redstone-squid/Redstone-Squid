"""A cog with commands to submit, view, confirm and deny submissions."""
# from __future__ import annotations  # dpy cannot resolve FlagsConverter with forward references :(

from collections.abc import Sequence
from typing import Literal, cast, TYPE_CHECKING, Any
import asyncio

import discord
from discord import InteractionResponse, Guild, Message
from discord.ext import commands
from discord.ext.commands import (
    Context,
    has_any_role,
    hybrid_group,
    Cog,
    hybrid_command,
    flag,
)
from postgrest import APIResponse
from pydantic import ValidationError
from typing_extensions import override

from bot import utils, config
from bot.vote_session import AbstractVoteSession, Vote
from bot.submission.ui import BuildSubmissionForm, ConfirmationView
from database import message as msg
from database.builds import get_all_builds, Build
from database import DatabaseManager
from database.enums import Status, Category
from bot._types import SubmissionCommandResponse, GuildMessageable
from bot.utils import RunningMessage, parse_dimensions, parse_build_title, remove_markdown
from database.message import get_build_id_by_message
from database.schema import TypeRecord
from database.server_settings import get_server_setting
from database.utils import upload_to_catbox, get_version_string

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

submission_roles = ["Admin", "Moderator", "Redstoner"]
APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]
# TODO: Set up a webhook for the bot to handle google form submissions.


class BuildVoteSession(AbstractVoteSession):
    """A vote session for a confirming or denying a build."""

    def __init__(self, build: Build, message: discord.Message, threshold: int = 3, negative_threshold: int = -3):
        super().__init__(message, threshold)
        self.build = build
        self.negative_threshold = negative_threshold
        embed = self.message.embeds[0]
        embed.add_field(name="upvotes", value=0)
        embed.add_field(name="downvotes", value=0)
        self.embed_upvote_index = len(embed.fields) - 2
        self.embed_downvote_index = len(embed.fields) - 1

    @override
    async def update_message(self):
        """Update the embed with new counts"""

        embed = self.message.embeds[0]
        embed.set_field_at(self.embed_upvote_index, name="upvotes", value=str(self.upvotes), inline=True)
        embed.set_field_at(self.embed_downvote_index, name="downvotes", value=str(self.downvotes), inline=True)
        await self.message.edit(embed=embed)


class SubmissionsCog(Cog, name="Submissions"):
    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot
        self.active_vote_sessions: dict[int, BuildVoteSession] = {}

    @hybrid_group(name="submissions", invoke_without_command=True)
    async def submission_hybrid_group(self, ctx: Context):
        """View, confirm and deny submissions."""
        await ctx.send_help("submissions")

    @submission_hybrid_group.command(name="pending")
    async def get_pending_submissions(self, ctx: Context):
        """Shows an overview of all submissions pending review."""
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

    @submission_hybrid_group.command(name="view")
    async def view_function(self, ctx: Context, submission_id: int):
        """Displays a submission."""
        async with utils.RunningMessage(ctx) as sent_message:
            submission = await Build.from_id(submission_id)

            if submission is None:
                error_embed = utils.error_embed("Error", "No submission with that ID.")
                return await sent_message.edit(embed=error_embed)

            return await sent_message.edit(embed=await submission.generate_embed())

    @staticmethod
    def is_owner_server(ctx: Context):
        if not ctx.guild or not ctx.guild.id == config.OWNER_SERVER_ID:
            # TODO: Make a custom error for this.
            # https://discordpy.readthedocs.io/en/stable/ext/commands/api.html?highlight=is_owner#discord.discord.ext.commands.on_command_error
            raise commands.CommandError("This command can only be executed on certain servers.")
        return True

    @submission_hybrid_group.command(name="confirm")
    @commands.check(is_owner_server)
    @has_any_role(*submission_roles)
    async def confirm_function(self, ctx: Context, submission_id: int):
        """Marks a submission as confirmed.

        This posts the submission to all the servers which configured the bot."""
        async with utils.RunningMessage(ctx) as sent_message:
            build = await Build.from_id(submission_id)

            if build is None:
                error_embed = utils.error_embed("Error", "No pending submission with that ID.")
                return await sent_message.edit(embed=error_embed)

            await build.confirm()
            await self.post_build(build, purpose="view_confirmed_build")

            success_embed = utils.info_embed("Success", "Submission has been confirmed.")
            return await sent_message.edit(embed=success_embed)

    @submission_hybrid_group.command(name="deny")
    @commands.check(is_owner_server)
    @has_any_role(*submission_roles)
    async def deny_function(self, ctx: Context, submission_id: int):
        """Marks a submission as denied."""
        async with utils.RunningMessage(ctx) as sent_message:
            build = await Build.from_id(submission_id)

            if build is None:
                error_embed = utils.error_embed("Error", "No pending submission with that ID.")
                return await sent_message.edit(embed=error_embed)

            await build.deny()

            success_embed = utils.info_embed("Success", "Submission has been denied.")
            return await sent_message.edit(embed=success_embed)

    # @submission_hybrid_group.command("send_all")
    # @has_any_role(*submission_roles)
    async def send_all(self, ctx: Context):
        """Sends all records and builds to this server, in the channels set."""
        # NOT in use right now
        async with utils.RunningMessage(ctx) as sent_message:
            assert ctx.guild is not None
            unsent_builds = await msg.get_unsent_builds(ctx.guild.id)

            for build in unsent_builds:
                await self.post_build(build, guilds=[ctx.guild], purpose="view_confirmed_build")

            success_embed = utils.info_embed("Success", "All posts have been sent.")
            return await sent_message.edit(embed=success_embed)

    @hybrid_command(name="versions")
    async def versions(self, ctx: Context):
        """Shows a list of versions the bot recognizes."""
        versions = await DatabaseManager.get_versions_list(edition="Java")
        versions_human_readable = [get_version_string(version) for version in versions[:20]]  # TODO: pagination
        await ctx.send(", ".join(versions_human_readable))

    async def post_build(self, build: Build, *, purpose: str, guilds: Sequence[Guild] | None = None) -> None:
        """Post a confirmed submission to the appropriate discord channels.

        Args:
            build (Build): The build to post.
            purpose (str): The purpose of the post.
            guilds (list[Guild], optional): The guilds to post to. If None, posts to all guilds.
        """
        # TODO: There are no checks to see if the submission has already been posted
        if build.id is None:
            raise ValueError("Build id is None.")

        if guilds is None:
            guilds = self.bot.guilds

        channel_ids = await build.get_channel_ids_to_post_to([guild.id for guild in guilds])
        em = await build.generate_embed()

        for channel_id in channel_ids:
            channel = self.bot.get_channel(channel_id)
            assert isinstance(channel, GuildMessageable)
            message = await channel.send(embed=em)
            await msg.track_message(channel.guild.id, build.id, message.channel.id, message.id, purpose)

            if purpose == "view_pending_build":
                # Add initial reactions
                try:
                    await message.add_reaction(APPROVE_EMOJIS[0])
                    await asyncio.sleep(1)
                    await message.add_reaction(DENY_EMOJIS[0])
                except discord.Forbidden:
                    pass  # Bot doesn't have permission to add reactions

                # Initialize the BuildVoteSession
                session = BuildVoteSession(build, message)
                self.active_vote_sessions[message.id] = session

    # fmt: off
    class SubmitFlags(commands.FlagConverter):
        """Parameters information for the /submit command."""

        door_size: str = flag(description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        record_category: Literal['Smallest', 'Fastest', 'First'] = flag(default=None, description='Is this build a record?')
        pattern: str = flag(default='Regular', description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default='Door', description='Door, Skydoor, or Trapdoor.')
        build_size: str | None = flag(default=None, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str = flag(
            # stupid workaround to get async code to work with flags
            default=get_version_string(asyncio.get_event_loop().run_until_complete(DatabaseManager.get_newest_version(edition="Java"))),
            description='The versions the build works in. Default to newest version. /versions for full list.'
        )
        wiring_placement_restrictions: str = flag(default=None, description='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.')
        component_restrictions: str = flag(default=None, description='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.')
        information_about_build: str = flag(default=None, description='Any additional information about the build.')
        normal_closing_time: int = flag(default=None, description='The time it takes to close the door, in gameticks. (1s = 20gt)')
        normal_opening_time: int = flag(default=None, description='The time it takes to open the door, in gameticks. (1s = 20gt)')
        date_of_creation: str = flag(default=None, description='The date the build was created.')
        in_game_name_of_creator: str = flag(default=None, description='The in-game name of the creator(s).')
        locationality: Literal["Locational", "Locational with fixes"] = flag(default=None, description='Whether the build works everywhere, or only in certain locations.')
        directionality: Literal["Directional", "Directional with fixes"] = flag(default=None, description='Whether the build works in all directions, or only in certain directions.')
        link_to_image: str = flag(default=None, description='A link to an image of the build. Use direct links only. e.g."https://i.imgur.com/abc123.png"')
        link_to_youtube_video: str = flag(default=None, description='A link to a video of the build.')
        link_to_world_download: str = flag(default=None, description='A link to download the world.')
    # fmt: on

    @commands.hybrid_command(name="submit")
    async def submit(self, ctx: Context, flags: SubmitFlags):
        """Submits a record to the database directly."""
        # TODO: Discord only allows 25 options. Split this into multiple commands.
        # FIXME: Discord WILL pass integers even if we specify a string. Need to convert them to strings.
        interaction = cast(discord.Interaction, ctx.interaction)
        response: InteractionResponse = interaction.response  # type: ignore
        await response.defer()

        followup: discord.Webhook = interaction.followup  # type: ignore

        async with RunningMessage(followup) as message:
            fmt_data = format_submission_input(ctx, cast(SubmissionCommandResponse, dict(flags)))
            build = Build.from_dict(fmt_data)

            # TODO: Stop hardcoding this
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
                f"Build submitted successfully!\nThe submission ID is: {build.id}",
            )
            await message.edit(embed=success_embed)
            await self.post_build(build, purpose="view_pending_build")

    class SubmitFormFlags(commands.FlagConverter):
        """Parameters information for the /submit command."""

        first_attachment: discord.Attachment = flag(default=None)
        second_attachment: discord.Attachment = flag(default=None)
        third_attachment: discord.Attachment = flag(default=None)
        fourth_attachment: discord.Attachment = flag(default=None)

    @commands.hybrid_command(name="submit_form")
    async def submit_form(self, ctx: Context, flags: SubmitFormFlags):
        """Submits a build to the database."""
        await ctx.defer()

        build = Build()
        for _name, attachment in flags:
            if attachment is None:
                continue

            assert isinstance(attachment, discord.Attachment)
            assert attachment.content_type is not None
            if not attachment.content_type.startswith("image") and not attachment.content_type.startswith("video"):
                raise ValueError(f"Unsupported content type: {attachment.content_type}")

            url = upload_to_catbox(attachment.filename, await attachment.read(), attachment.content_type)
            if attachment.content_type.startswith("image"):  # pyright: ignore [reportOptionalMemberAccess]
                build.image_urls.append(url)
            elif attachment.content_type.startswith("video"):  # pyright: ignore [reportOptionalMemberAccess]
                build.video_urls.append(url)

        view = BuildSubmissionForm(build)
        followup: discord.Webhook = ctx.interaction.followup  # type: ignore

        await followup.send("Use the select menus then click the button", view=view)
        await view.wait()
        if view.value is None:
            return await followup.send("Submission canceled due to inactivity", ephemeral=True)
        elif view.value is False:
            return await followup.send("Submission canceled by user", ephemeral=True)
        else:
            await build.save()
            await followup.send(
                "Here is a preview of the submission. Use /edit if you have made a mistake",
                embed=await build.generate_embed(),
                ephemeral=True,
            )
            await self.post_build(build, purpose="view_pending_build")

    # fmt: off
    class EditFlags(commands.FlagConverter):
        """Parameters information for the /edit command."""
        build_id: int = flag(description='The ID of the submission.')
        door_width: int = flag(default=None, description='The width of the door itself. Like 2x2 piston door.')
        door_height: int = flag(default=None, description='The height of the door itself. Like 2x2 piston door.')
        pattern: str = flag(default=None, description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default=None, description='Door, Skydoor, or Trapdoor.')
        build_size: str | None = flag(default=None, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str = flag(default=None, description='The versions the build works in. Default to newest version. /versions for full list.')
        wiring_placement_restrictions: str = flag(default=None, description='For example, "Seamless, Full Flush". See the regulations (/docs) for the complete list.')
        component_restrictions: str = flag(default=None, description='For example, "No Pistons, No Slime Blocks". See the regulations (/docs) for the complete list.')
        information_about_build: str = flag(default=None, description='Any additional information about the build.')
        normal_closing_time: int = flag(default=None, description='The time it takes to close the door, in gameticks. (1s = 20gt)')
        normal_opening_time: int = flag(default=None, description='The time it takes to open the door, in gameticks. (1s = 20gt)')
        date_of_creation: str = flag(default=None, description='The date the build was created.')
        in_game_name_of_creator: str = flag(default=None, description='The in-game name of the creator(s).')
        locationality: Literal["Locational", "Locational with fixes"] = flag(default=None, description='Whether the build works everywhere, or only in certain locations.')
        directionality: Literal["Directional", "Directional with fixes"] = flag(default=None, description='Whether the build works in all directions, or only in certain directions.')
        link_to_image: str = flag(default=None, description='A link to an image of the build. Use direct links only. e.g."https://i.imgur.com/abc123.png"')
        link_to_youtube_video: str = flag(default=None, description='A link to a video of the build.')
        link_to_world_download: str = flag(default=None, description='A link to download the world.')
        server_ip: str = flag(default=None, description='The IP of the server where the build is located.')
        coordinates: str = flag(default=None, description='The coordinates of the build in the server.')
        command_to_get_to_build: str = flag(default=None, description='The command to get to the build in the server.')
    # fmt: on

    @commands.hybrid_command(name="edit")
    async def edit(self, ctx: Context, flags: EditFlags):
        """Edits a record in the database directly."""
        interaction = ctx.interaction
        assert interaction is not None

        response: InteractionResponse = interaction.response  # type: ignore
        await response.defer()

        followup: discord.Webhook = interaction.followup  # type: ignore
        async with RunningMessage(followup) as sent_message:
            submission = await Build.from_id(flags.build_id)
            if submission is None:
                error_embed = utils.error_embed("Error", "No submission with that ID.")
                return await sent_message.edit(embed=error_embed)

            update_values = format_submission_input(ctx, cast(SubmissionCommandResponse, dict(flags)))
            submission.update_local(update_values)
            preview_embed = await submission.generate_embed()

            # Show a preview of the changes and ask for confirmation
            await sent_message.edit(embed=utils.info_embed("Waiting", "User confirming changes..."))
            view = ConfirmationView()
            preview = await followup.send(embed=preview_embed, view=view, ephemeral=True, wait=True)
            await view.wait()

            await preview.delete()
            if view.value is None:
                await sent_message.edit(embed=utils.info_embed("Timed out", "Build edit canceled due to inactivity."))
            elif view.value:
                await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
                await submission.save()
                await self.update_build_messages(submission)
                await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))
            else:
                await sent_message.edit(embed=utils.info_embed("Cancelled", "Build edit canceled by user"))

    async def update_build_message(self, build: Build, channel_id: int, message_id: int) -> None:
        """Updates a post according to the information given by the build."""
        if await get_build_id_by_message(message_id) != build.id:
            raise ValueError("The message_id does not correspond to the build_id.")

        em = await build.generate_embed()
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.PartialMessageable):
            raise ValueError(f"Invalid channel type for a post channel: {type(channel)}")

        message = await channel.fetch_message(message_id)
        await message.edit(embed=em)
        await msg.update_message_edited_time(message.id)

    async def update_build_messages(self, build: Build) -> None:
        """Updates all messages which are posts for a build."""
        if build.id is None:
            raise ValueError("Build id is None.")

        # Get all messages for a build
        messages = await msg.get_build_messages(build.id)
        em = await build.generate_embed()

        for message in messages:
            channel = self.bot.get_channel(message["channel_id"])
            if not isinstance(channel, discord.PartialMessageable):
                raise ValueError(f"Invalid channel type for a post channel: {type(channel)}")

            message = await channel.fetch_message(message["message_id"])
            await message.edit(embed=em)
            await msg.update_message_edited_time(message.id)

    @commands.hybrid_command()
    async def list_patterns(self, ctx: Context):
        """Lists all the available patterns."""
        async with RunningMessage(ctx) as sent_message:
            patterns: APIResponse[TypeRecord] = await DatabaseManager().table("types").select("*").execute()
            names = [pattern["name"] for pattern in patterns.data]
            await sent_message.edit(
                content="Here are the available patterns:", embed=utils.info_embed("Patterns", ", ".join(names))
            )

    @Cog.listener(name="on_raw_reaction_add")
    async def confirm_or_deny_build_by_reaction(self, payload: discord.RawReactionActionEvent):
        """Handles anonymous voting with initial reactions."""

        vote = await self._validate_vote(payload)
        if vote is None:
            return

        # Remove the user's reaction to keep votes anonymous
        try:
            await vote.message.remove_reaction(payload.emoji, vote.user)
        except (discord.Forbidden, discord.NotFound):
            pass  # Ignore if we can't remove the reaction

        # Get the BuildVoteSession
        session = self.active_vote_sessions.get(payload.message_id)
        if not session:
            raise ValueError("No active vote session found for this message.")

        # Update votes based on the reaction
        emoji_name = str(payload.emoji)
        user_id = payload.user_id

        original_vote = session.votes.get(user_id, 0)
        if emoji_name in APPROVE_EMOJIS:
            session.votes[user_id] = 1 if original_vote != 1 else 0
        elif emoji_name in DENY_EMOJIS:
            session.votes[user_id] = -1 if original_vote != -1 else 0
        else:
            return

        # Check thresholds and act accordingly
        if session.net_votes >= session.threshold:
            await vote.build.confirm()
            del self.active_vote_sessions[payload.message_id]
            await self._remove_vote_messages(vote.build)
        elif session.net_votes <= session.negative_threshold:
            await vote.build.deny()
            del self.active_vote_sessions[payload.message_id]
            await self._remove_vote_messages(vote.build)
        else:
            await session.update_message()

    async def _remove_vote_messages(self, build: Build):
        """Removes all messages associated with votes for a build."""

        message_records = await msg.untrack_message(build_id=build.id, purpose="view_pending_build")
        for record in message_records:
            try:
                channel = self.bot.get_channel(record["channel_id"])
                msg_to_delete = await channel.fetch_message(record["message_id"])
                await msg_to_delete.delete()
            except discord.NotFound:
                pass  # Message already deleted

    async def _validate_vote(self, payload: discord.RawReactionActionEvent) -> Vote | None:
        """Check if a reaction is a valid vote."""

        # Must be in a guild
        if (guild_id := payload.guild_id) is None:
            return

        # Ignore bot reactions
        user = self.bot.get_user(payload.user_id)
        if user is None:
            user = await self.bot.fetch_user(payload.user_id)
        if user.bot:
            return

        # Must be in the vote channel
        vote_channel_id = await get_server_setting(guild_id, "Vote")
        if vote_channel_id is None or payload.channel_id != vote_channel_id:
            return

        # The message must be from the bot
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(payload.channel_id)
        message: Message = await channel.fetch_message(payload.message_id)

        if message.author.id != self.bot.user.id:
            return

        # A build ID must be associated with the message
        build_id = await msg.get_build_id_by_message(payload.message_id)
        if build_id is None:
            return

        # The build status must be pending
        build = await Build.from_id(build_id)
        if build is None or build.submission_status != Status.PENDING:
            return

        return Vote(guild=self.bot.get_guild(guild_id), channel=channel, message=message, build=build, user=user)

    # @Cog.listener(name="on_message")
    async def infer_build_from_title(self, message: Message):
        """Infer a build from a message."""
        if message.author.bot:
            return

        if message.channel.id not in [726156829629087814, 667401499554611210, 536004554743873556]:
            return

        title_str = remove_markdown(message.content).splitlines()[0]
        try:
            title = await parse_build_title(title_str, mode="ai" if len(title_str) <= 300 else "manual")
        except ValidationError:
            return

        # build = Build()
        # build.record_category = title.record_category
        # build.category = "Door"
        # build.component_restrictions = title.component_restrictions
        # build.door_width = title.door_width
        # build.door_height = title.door_height
        # build.door_depth = title.door_depth
        # build.wiring_placement_restrictions = title.wiring_placement_restrictions
        # build.door_types = title.door_types
        # build.door_orientation_type = title.orientation
        # print(title)

        bot_channel = self.bot.get_channel(536004554743873556)
        if title:
            await bot_channel.send(title.model_dump_json())  # type: ignore
        else:
            await bot_channel.send("No title found")  # type: ignore


def format_submission_input(ctx: Context, data: SubmissionCommandResponse) -> dict[str, Any]:
    """Formats the submission data from what is passed in commands to something recognizable by Build."""
    # Union of all the /submit and /edit command options
    parsable_signatures = SubmissionCommandResponse.__annotations__.keys()
    if not all(key in parsable_signatures for key in data):
        unknown_keys = [key for key in data if key not in parsable_signatures]
        raise ValueError(
            f"found unknown keys {unknown_keys} in data, did the command signature of /submit or /edit change?"
        )

    fmt_data: dict[str, Any] = dict()
    fmt_data["id"] = data.get("submission_id")
    # fmt_data['submission_status']

    fmt_data["record_category"] = data.get("record_category")
    if (works_in := data.get("works_in")) is not None:
        fmt_data["functional_versions"] = works_in.split(", ")
    else:
        fmt_data["functional_versions"] = []

    if (build_size := data.get("build_size")) is not None:
        build_dimensions = parse_dimensions(build_size)
        fmt_data["width"], fmt_data["height"], fmt_data["depth"] = build_dimensions

    if (door_size := data.get("door_size")) is not None:
        door_dimensions = parse_dimensions(door_size)
        fmt_data["door_width"], fmt_data["door_height"], fmt_data["door_depth"] = door_dimensions

    if (pattern := data.get("pattern")) is not None:
        fmt_data["door_type"] = pattern.split(", ")
    fmt_data["door_orientation_type"] = data.get("door_type")

    if (wp_res := data.get("wiring_placement_restrictions")) is not None:
        fmt_data["wiring_placement_restrictions"] = wp_res.split(", ")
    else:
        fmt_data["wiring_placement_restrictions"] = []

    if (co_res := data.get("component_restrictions")) is not None:
        fmt_data["component_restrictions"] = co_res.split(", ")
    else:
        fmt_data["component_restrictions"] = []
    misc_restrictions = [data.get("locationality"), data.get("directionality")]
    fmt_data["miscellaneous_restrictions"] = [x for x in misc_restrictions if x is not None]

    fmt_data["normal_closing_time"] = data.get("normal_closing_time")
    fmt_data["normal_opening_time"] = data.get("normal_opening_time")
    # fmt_data['visible_closing_time']
    # fmt_data['visible_opening_time']

    information_dict = (
        {"user": data.get("information_about_build")} if data.get("information_about_build") is not None else None
    )
    fmt_data["information"] = information_dict
    if (ign := data.get("in_game_name_of_creator")) is not None:
        fmt_data["creators_ign"] = ign.split(", ")
    else:
        fmt_data["creators_ign"] = []

    fmt_data["image_urls"] = data.get("link_to_image")
    fmt_data["video_urls"] = data.get("link_to_youtube_video")
    fmt_data["world_download_urls"] = data.get("link_to_world_download")

    fmt_data["server_ip"] = data.get("server_ip")
    fmt_data["coordinates"] = data.get("coordinates")
    fmt_data["command"] = data.get("command_to_get_to_build")

    fmt_data["submitter_id"] = ctx.author.id
    fmt_data["completion_time"] = data.get("date_of_creation")
    # fmt_data['edited_time'] = get_current_utc()

    fmt_data = {k: v for k, v in fmt_data.items() if v is not None}
    return fmt_data


async def setup(bot: "RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SubmissionsCog(bot))
