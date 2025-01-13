"""A cog with commands to submit, view, confirm and deny submissions."""
# from __future__ import annotations  # dpy cannot resolve FlagsConverter with forward references :(

from collections.abc import Iterable
from typing import Literal, cast, TYPE_CHECKING, Any, final
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
)
from openai import AsyncOpenAI
from postgrest.base_request_builder import APIResponse, SingleAPIResponse
from typing_extensions import override
import vecs

from bot import utils
from bot.submission.parse import parse_dimensions
from bot.vote_session import AbstractVoteSession
from bot.submission.ui import BuildSubmissionForm, ConfirmationView
from database import message as msg
from database.builds import get_all_builds, Build
from database import DatabaseManager
from database.enums import Status, Category
from bot._types import GuildMessageable
from bot.utils import RunningMessage, is_owner_server, check_is_staff, check_is_trusted_or_staff, is_staff
from database.message import get_build_id_by_message
from database.schema import TypeRecord, RestrictionRecord
from database.utils import upload_to_catbox
from database.vote import track_build_vote_session, track_vote_session, close_vote_session

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]
# TODO: Set up a webhook for the bot to handle google form submissions.


@final
class BuildVoteSession(AbstractVoteSession):
    """A vote session for a confirming or denying a build."""

    kind = "build"

    def __init__(
        self,
        bot: discord.Client,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ):
        """
        Initialize the vote session.

        Args:
            bot: The discord client.
            messages: The messages belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            build: The build which the vote session is for. If type is "update", this is the updated build.
            type: Whether to add or update the build.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold)
        self.build = build
        self.type = type

    @classmethod
    @override
    async def create(
        cls,
        bot: discord.Client,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> "BuildVoteSession":
        self = await super().create(bot, messages, author_id, build, type, pass_threshold, fail_threshold)
        assert isinstance(self, BuildVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        self.id = await track_vote_session(
            await self.fetch_messages(),
            self.author_id,
            self.kind,
            self.pass_threshold,
            self.fail_threshold,
            build_id=self.build.id,
        )
        await self.update_messages()

        reaction_tasks = [message.add_reaction(APPROVE_EMOJIS[0]) for message in self._messages]
        reaction_tasks.extend([message.add_reaction(DENY_EMOJIS[0]) for message in self._messages])
        try:
            await asyncio.gather(*reaction_tasks)
        except discord.Forbidden:
            pass  # Bot doesn't have permission to add reactions

        assert self.build.id is not None
        if self.type == "add":
            changes = [("submission_status", Status.PENDING, Status.CONFIRMED)]
        else:
            original = await Build.from_id(self.build.id)
            assert original is not None
            changes = original.diff(self.build)
        await track_build_vote_session(self.id, self.build.id, changes)

    @classmethod
    @override
    async def from_id(cls, bot: discord.Client, vote_session_id: int) -> "BuildVoteSession | None":
        db = DatabaseManager()
        vote_session_response: SingleAPIResponse[dict[str, Any]] | None = (
            await db.table("vote_sessions")
            .select("*, messages(*), votes(*), build_vote_sessions(*)")
            .eq("id", vote_session_id)
            .eq("kind", cls.kind)
            .maybe_single()
            .execute()
        )
        if vote_session_response is None:
            return None

        vote_session_record = vote_session_response.data

        build_id = vote_session_record["build_vote_sessions"]["build_id"]
        build = await Build.from_id(build_id)
        if build is None:
            raise ValueError(
                f"The message record for this vote session is associated with a non-existent build id: {build_id}."
            )

        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            bot,
            [record["message_id"] for record in vote_session_record["messages"]],
            vote_session_record["author_id"],
            build,
            "add",  # FIXME: Stop hardcoding this
            vote_session_record["pass_threshold"],
            vote_session_record["fail_threshold"],
        )
        self.id = vote_session_id  # We can skip _async_init because we already have the id and everything has been tracked before
        return self

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        message = await channel.send(content=self.build.original_link, embed=await self.build.generate_embed())
        await msg.track_message(message, purpose="vote", build_id=self.build.id, vote_session_id=self.id)
        self._messages.add(message)
        return message

    @override
    async def update_messages(self):
        embed = await self.build.generate_embed()
        embed.add_field(name="", value="", inline=False)  # Add a blank field to separate the vote count
        embed.add_field(name="Accept", value=f"{self.upvotes}/{self.pass_threshold}", inline=True)
        embed.add_field(name="Deny", value=f"{self.downvotes}/{-self.fail_threshold}", inline=True)
        await asyncio.gather(
            *[message.edit(content=self.build.original_link, embed=embed) for message in await self.fetch_messages()]
        )

    @override
    async def close(self) -> None:
        if self.is_closed:
            return

        self.is_closed = True
        if self.net_votes < self.pass_threshold:
            await self.build.deny()
        else:
            await self.build.confirm()
        # TODO: decide whether to delete the messages or not

        await self.update_messages()

        if self.id is not None:
            await close_vote_session(self.id)

    @classmethod
    async def get_open_vote_sessions(cls: type["BuildVoteSession"], bot: discord.Client) -> list["BuildVoteSession"]:
        """Get all open vote sessions from the database."""
        db = DatabaseManager()
        records = (
            await db.table("vote_sessions")
            .select("*, messages(*), votes(*), build_vote_sessions(*)")
            .eq("status", "open")
            .eq("kind", cls.kind)
            .execute()
        ).data

        async def _get_session(record: dict[str, Any]) -> "BuildVoteSession":
            if record["build_vote_sessions"] is None:
                raise ValueError(f"Found a build vote session with no associated build id. {record["id"]=}")
            build_id: int = record["build_vote_sessions"]["build_id"]
            build = await Build.from_id(build_id)

            assert build is not None
            session = cls.__new__(cls)
            session._allow_init = True
            session.__init__(
                bot=bot,
                messages=[msg["message_id"] for msg in record["messages"]],
                author_id=record["author_id"],
                build=build,
                type="add",
                pass_threshold=record["pass_threshold"],
                fail_threshold=record["fail_threshold"],
            )
            session.id = record["id"]
            session._votes = {vote["user_id"]: vote["weight"] for vote in record["votes"]}

            return session

        return await asyncio.gather(*[_get_session(record) for record in records])


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

    # fmt: off
    class SubmitDoorFlags(commands.FlagConverter):
        """Parameters information for the /submit door command."""

        def to_build(self) -> Build:
            """Convert the flags to a build object."""
            build = Build()
            build.record_category = self.record_category
            build.version_spec = self.works_in
            build.versions = DatabaseManager.find_versions_from_spec(self.works_in)

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
            build.world_download_urls = (
                [self.link_to_world_download] if self.link_to_world_download is not None else []
            )
            build.completion_time = self.date_of_creation
            return build

        # Intentionally moved closer to the submit command
        door_size: str = flag(description='e.g. *2x2* piston door. In width x height (x depth), spaces optional.')
        record_category: Literal['Smallest', 'Fastest', 'First'] = flag(default=None, description='Is this build a record?')
        pattern: str = flag(default='Regular', description='The pattern type of the door. For example, "full lamp" or "funnel".')
        door_type: Literal['Door', 'Skydoor', 'Trapdoor'] = flag(default='Door', description='Door, Skydoor, or Trapdoor.')
        build_size: str | None = flag(default=None, description='The dimension of the build. In width x height (x depth), spaces optional.')
        works_in: str = flag(
            default=DatabaseManager.get_newest_version(edition="Java"),
            description='Specify the versions the build works in. The format should be like "1.17 - 1.18.1, 1.20+".'
        )
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
                build.versions = DatabaseManager.find_versions_from_spec(works_in)
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
                    await self.update_build_messages(build)
                    await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))
                else:
                    await sent_message.edit(embed=utils.info_embed("Cancelled", "Build edit canceled by user"))
            else:  # Not an interaction, so we can't use buttons for confirmation
                await sent_message.edit(embed=utils.info_embed("Editing", "Editing build..."))
                await build.save()
                await self.update_build_messages(build)
                await sent_message.edit(embed=utils.info_embed("Success", "Build edited successfully"))

    async def update_build_message(self, build: Build, channel_id: int, message_id: int) -> None:
        """Updates a post according to the information given by the build."""
        if await get_build_id_by_message(message_id) != build.id:
            raise ValueError("The message_id does not correspond to the build_id.")

        em = await build.generate_embed()
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.PartialMessageable):
            raise ValueError(f"Invalid channel type for a post channel: {type(channel)}")

        message = await channel.fetch_message(message_id)
        await message.edit(content=build.original_link, embed=em)
        await msg.update_message_edited_time(message.id)

    async def update_build_messages(self, build: Build) -> None:
        """Updates all messages which are posts for a build."""
        if build.id is None:
            raise ValueError("Build id is None.")

        # Get all messages for a build
        message_records = await msg.get_build_messages(build.id)
        em = await build.generate_embed()

        for record in message_records:
            message = await utils.getch(self.bot, record)
            if message is None:
                continue
            await message.edit(content=build.original_link, embed=em)
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
    async def update_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        if (vote_session := self.open_vote_sessions.get(payload.message_id)) is None:
            return

        if vote_session.is_closed:
            for message_id in vote_session.message_ids:
                self.open_vote_sessions.pop(message_id, None)

        # Remove the user's reaction to keep votes anonymous
        channel = cast(GuildMessageable, self.bot.get_channel(payload.channel_id))
        message = await channel.fetch_message(payload.message_id)
        user = self.bot.get_user(payload.user_id)
        assert user is not None
        try:
            await message.remove_reaction(payload.emoji, user)
        except (discord.Forbidden, discord.NotFound):
            pass  # Ignore if we can't remove the reaction

        # Update votes based on the reaction
        emoji_name = str(payload.emoji)
        user_id = payload.user_id

        # The vote session will handle the closing of the vote session
        original_vote = vote_session[user_id]
        weight = await self.get_voting_weight(payload.guild_id, user_id)
        if emoji_name in APPROVE_EMOJIS:
            vote_session[user_id] = weight if original_vote != weight else 0
        elif emoji_name in DENY_EMOJIS:
            vote_session[user_id] = -weight if original_vote != -weight else 0
        else:
            return
        await vote_session.update_messages()

    async def get_voting_weight(self, server_id: int | None, user_id: int) -> float:
        """Get the voting weight of a user."""
        if await is_staff(self.bot, server_id, user_id):
            return 3
        return 1

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
            await DatabaseManager().table("restriction_aliases").insert({"restriction_id": restriction_id, "alias": alias}).execute()
            await sent_message.edit(embed=utils.info_embed("Success", "Alias added."))

    @commands.command("search_restrictions")
    @check_is_staff()
    @commands.check(is_owner_server)
    async def search_restrictions(self, ctx: Context, query: str | None):
        """This runs a substring search on the restriction names."""
        async with RunningMessage(ctx) as sent_message:
            if query:
                response: APIResponse[RestrictionRecord] = await DatabaseManager().table("restrictions").select("*").ilike("name", f"%{query}%").execute()
            else:
                response = await DatabaseManager().table("restrictions").select("*").execute()
            restrictions = response.data
            description = "\n".join([f"{restriction['id']}: {restriction['name']}" for restriction in restrictions])
            await sent_message.edit(embed=utils.info_embed("Restrictions", description))


async def setup(bot: "RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    cog = BuildCog(bot)
    open_vote_sessions = await BuildVoteSession.get_open_vote_sessions(bot)
    for session in open_vote_sessions:
        for message_id in session.message_ids:
            cog.open_vote_sessions[message_id] = session

    await bot.add_cog(cog)
