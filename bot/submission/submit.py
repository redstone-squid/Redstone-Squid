"""A cog with commands to submit, view, confirm and deny submissions."""
# from __future__ import annotations  # dpy cannot resolve FlagsConverter with forward references :(

from collections.abc import Sequence
from textwrap import dedent
from typing import Literal, cast, TYPE_CHECKING, Any

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

from bot import utils, config
from bot.submission.ui import BuildSubmissionForm, ConfirmationView
from database import message as msg
from database.builds import get_all_builds, Build
from database.database import DatabaseManager
from database.enums import Status, Category
from bot._types import SubmissionCommandResponse, GuildMessageable
from bot.utils import RunningMessage, parse_dimensions, parse_build_title
from database.message import get_build_id_by_message
from database.schema import TypeRecord
from database.server_settings import get_server_setting
from database.utils import upload_to_catbox

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

submission_roles = ["Admin", "Moderator", "Redstoner"]
# TODO: Set up a webhook for the bot to handle google form submissions.


class SubmissionsCog(Cog, name="Submissions"):
    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot

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
                        # FIXME: sub.creators_ign is assumed to be a list, but it's a string
                        f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators_ign))}_ - _submitted by {sub.submitter_id}_"  # type: ignore
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
                error_embed = utils.error_embed("Error", "No open submission with that ID.")
                return await sent_message.edit(embed=error_embed)

            return await sent_message.edit(embed=submission.generate_embed())

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
            await self.post_build(build)

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
                await self.post_build(build, guilds=[ctx.guild])

            success_embed = utils.info_embed("Success", "All posts have been sent.")
            return await sent_message.edit(embed=success_embed)

    @hybrid_command(name="versions")
    async def versions(self, ctx: Context):
        """Shows a list of versions the bot recognizes."""
        await ctx.send(str(config.VERSIONS_LIST))

    # fmt: off
    class SubmitFlags(commands.FlagConverter):
        """Parameters information for the /submit command."""
        door_size: str = flag(description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        record_category: Literal['Smallest', 'Fastest', 'First'] = flag(default=None, description='Is this build a record?')
        pattern: str = flag(default='Regular', description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default='Door', description='Door, Skydoor, or Trapdoor.')
        build_size: str | None = flag(default=None, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str = flag(default=config.VERSIONS_LIST[-1], description='The versions the build works in. Default to newest version. /versions for full list.')
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
                embed=build.generate_embed(),
                ephemeral=True,
            )

            success_embed = utils.info_embed(
                "Success",
                f"Build submitted successfully!\nThe submission ID is: {build.id}",
            )
            await message.edit(embed=success_embed)
            await self.post_build(build)

    async def post_build(self, build: Build, *, guilds: Sequence[Guild] | None = None) -> None:
        """Posts a submission to the appropriate discord channels.

        Args:
            build (Build): The build to post.
            guilds (list[Guild], optional): The guilds to post to. If None, posts to all guilds. Defaults to None.
        """
        # TODO: There are no checks to see if the submission has already been posted, or if the submission is actually a record
        if build.id is None:
            raise ValueError("Build id is None.")

        if guilds is None:
            guilds = self.bot.guilds

        channel_ids = await build.get_channel_ids_to_post_to([guild.id for guild in guilds])
        em = build.generate_embed()

        for channel_id in channel_ids:
            channel = self.bot.get_channel(channel_id)
            assert isinstance(channel, GuildMessageable)
            message = await channel.send(embed=em)
            await msg.add_message(channel.guild.id, build.id, message.channel.id, message.id, "build_post")

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
        for name, attachment in flags:
            if attachment is None:
                continue

            assert isinstance(attachment, discord.Attachment)
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
                embed=build.generate_embed(),
                ephemeral=True,
            )
            await self.post_build(build)

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
            preview_embed = submission.generate_embed()

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

        em = build.generate_embed()
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
        em = build.generate_embed()

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
    async def confirm_record(self, payload: discord.RawReactionActionEvent):
        """Listens for reactions on the vote channel and confirms the submission if the reaction is a thumbs up."""
        # --- A bunch of checks to make sure the reaction is valid ---
        # Must be in a guild
        if (guild_id := payload.guild_id) is None:
            return

        # Must be in the vote channel
        vote_channel_id = await get_server_setting(guild_id, "Vote")
        if vote_channel_id is None or payload.channel_id != vote_channel_id:
            return

        # Must be users that are allowed to vote
        if payload.user_id != config.OWNER_ID:
            return

        # The message must be from the bot
        message: discord.Message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)  # type: ignore[attr-defined]
        if message.author.id != self.bot.user.id:  # type: ignore[attr-defined]
            return

        # A build ID must be associated with the message
        build_id = await msg.get_build_id_by_message(payload.message_id)
        if build_id is None:
            return

        # The submission status must be pending
        submission = await Build.from_id(build_id)
        assert submission is not None
        if submission.submission_status != Status.PENDING:
            return
        # --- End of checks ---

        # If the reaction is a thumbs up, confirm the submission
        if payload.emoji.name == "👍":
            # TODO: Count the number of thumbs up reactions and confirm if it passes a threshold
            await submission.confirm()
            message_ids = await msg.delete_message(guild_id, build_id)
            await self.post_build(submission)
            for message_id in message_ids:
                vote_channel = self.bot.get_channel(vote_channel_id)
                if isinstance(vote_channel, GuildMessageable):
                    message = await vote_channel.fetch_message(message_id)
                    await message.delete()
                else:
                    # TODO: Add a check when adding vote channels to the database
                    raise ValueError(f"Invalid channel type for a vote channel: {type(vote_channel)}")

    @Cog.listener(name="on_message")
    async def suggest_parameters_from_title(self, message: Message):
        """Suggests parameters from the title of the submission."""
        if message.author.bot:
            return

        if message.channel.id not in [726156829629087814, 667401499554611210, 536004554743873556]:
            return

        title_str = message.content.splitlines()[0]
        try:
            title, unparsed = await parse_build_title(title_str)
        except ValidationError:
            return

        content = dedent(f"""
        **Record Category**: {title.record_category}
        **Component Restrictions**: {title.component_restrictions}
        **Door Size**: {title.door_size}
        **Wiring Placement Restrictions**: {title.wiring_placement_restrictions}
        **Door Type**: {title.door_types}
        **Orientation**: {title.orientation}
        
        **Unparsed**: {unparsed}
        """)
        await self.bot.get_channel(536004554743873556).send(content)


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
