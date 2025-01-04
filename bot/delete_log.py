import asyncio
from textwrap import dedent

import discord
from discord.ext.commands import command, Cog, Context
from typing import TYPE_CHECKING, Any, final

from postgrest.base_request_builder import SingleAPIResponse
from typing_extensions import override

from bot import utils
from bot._types import GuildMessageable
from bot.vote_session import AbstractVoteSession
from bot.utils import check_is_staff
from database import DatabaseManager
from database.server_settings import get_server_setting
from database.vote import track_vote_session, track_delete_log_vote_session, close_vote_session

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

APPROVE_EMOJI = "✅"
DENY_EMOJI = "❌"


@final
class DeleteLogVoteSession(AbstractVoteSession):
    """A vote session for deleting a message from the log."""

    kind = "delete_log"

    def __init__(
        self,
        message: discord.Message,
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ):
        """
        Initialize the delete log vote session.

        Args:
            message: The message to track votes on.
            author_id: The discord id of the author of the vote session.
            target_message: The message to delete if the vote passes.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        super().__init__(message, author_id, pass_threshold, fail_threshold)
        self.target_message = target_message

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        self.id = await track_vote_session(self.message, self.author_id, self.kind, self.pass_threshold, self.fail_threshold)
        await track_delete_log_vote_session(self.id, self.target_message)
        await self.update_message()

    @classmethod
    @override
    async def from_id(cls, bot: discord.Client, vote_session_id: int) -> "DeleteLogVoteSession | None":
        db = DatabaseManager()
        vote_session_response: SingleAPIResponse[dict[str, Any]] | None = (
            await db.table("vote_sessions").select("*, messages(*), delete_log_vote_sessions(*)").eq("id", vote_session_id).eq("kind", cls.kind).maybe_single().execute()
        )
        if vote_session_response is None:
            return None

        vote_session_record = vote_session_response.data
        message_id = vote_session_record["messages"][0]["message_id"]
        channel_id = vote_session_record["messages"][0]["channel_id"]
        channel = bot.get_channel(channel_id)
        assert isinstance(channel, GuildMessageable)
        message = await channel.fetch_message(message_id)

        target_message_id = vote_session_record["delete_log_vote_sessions"][0]["target_message_id"]
        target_channel_id = vote_session_record["delete_log_vote_sessions"][0]["target_channel_id"]
        target_channel = bot.get_channel(target_channel_id)
        assert isinstance(target_channel, GuildMessageable)
        target_message = await target_channel.fetch_message(target_message_id)

        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            message,
            vote_session_record["author_id"],
            target_message,
            vote_session_record["pass_threshold"],
            vote_session_record["fail_threshold"],
        )
        self.id = vote_session_id  # We can skip _async_init because we already have the id and everything has been tracked before
        return self

    @classmethod
    @override
    async def create(
        cls,
        message: discord.Message,
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> "DeleteLogVoteSession":
        self = await super().create(message, author_id, target_message, pass_threshold, fail_threshold)
        assert isinstance(self, DeleteLogVoteSession)
        return self

    @override
    async def update_message(self) -> None:
        """Updates the message with the current vote count."""
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=(
                dedent(f"""
                React with {APPROVE_EMOJI} to upvote or {DENY_EMOJI} to downvote.\n\n
                **Log Content:**\n{self.target_message.content}\n\n
                **Upvotes:** {self.upvotes}
                **Downvotes:** {self.downvotes}
                **Net Votes:** {self.net_votes}""")
            ),
        )
        await self.message.edit(embed=embed)

    @override
    async def close(self) -> None:
        if self.is_closed:
            return

        self.is_closed = True
        if self.net_votes <= self.pass_threshold:
            await self.message.channel.send("Vote failed.")
        else:
            await self.target_message.delete()

        if self.id is not None:
            await close_vote_session(self.id)

    @classmethod
    async def get_open_vote_sessions(cls: type["DeleteLogVoteSession"], bot: discord.Client) -> list["DeleteLogVoteSession"]:
        """Get all open vote sessions from the database."""
        db = DatabaseManager()
        records = (await db.table("vote_sessions").select("*, messages(*), votes(*), delete_log_vote_sessions(*)").eq("status", "open").eq("kind", cls.kind).execute()).data

        sessions = []
        for record in records:
            message_id = record["messages"][0]["message_id"]
            channel_id = record["messages"][0]["channel_id"]
            channel = await bot.fetch_channel(channel_id)
            assert isinstance(channel, GuildMessageable)
            message = await channel.fetch_message(message_id)

            target_message_id = record["delete_log_vote_sessions"]["target_message_id"]
            target_channel_id = record["delete_log_vote_sessions"]["target_channel_id"]
            target_channel = await bot.fetch_channel(target_channel_id)
            assert isinstance(target_channel, GuildMessageable)
            target_message = await target_channel.fetch_message(target_message_id)

            session = cls.__new__(cls)
            session._allow_init = True
            session.__init__(
                message,
                record["author_id"],
                target_message,
                record["pass_threshold"],
                record["fail_threshold"],
            )
            session.id = record["id"]
            session._votes = {vote["user_id"]: vote["weight"] for vote in record["votes"]}

            sessions.append(session)

        return sessions


class DeleteLogCog(Cog, name="Vote"):
    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot
        self.open_vote_sessions: dict[int, DeleteLogVoteSession] = {}

    @command(name="test_role")
    @check_is_staff()
    async def test_role(self, ctx: Context):
        """Test command to check role-based access."""
        print("You have the role")

    @command(name="start_vote")
    async def start_vote(self, ctx: Context, target_message: discord.Message):
        """Starts a vote to delete a specified message by providing its URL."""
        # Check if guild_id matches the current guild
        if ctx.guild != target_message.guild:
            await ctx.send("The message is not from this guild.")
            return

        async with utils.RunningMessage(ctx) as message:
            # Add initial reactions
            await message.add_reaction(APPROVE_EMOJI)
            await asyncio.sleep(1)
            await message.add_reaction(DENY_EMOJI)
            vote_session = await DeleteLogVoteSession.create(message, author_id=ctx.author.id, target_message=target_message)
            self.open_vote_sessions[message.id] = vote_session

    @Cog.listener("on_raw_reaction_add")
    async def update_delete_log_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        user = self.bot.get_user(payload.user_id)
        assert user is not None

        if user.bot:
            return  # Ignore bot reactions

        if payload.guild_id is None:
            return

        # Check if the message is being tracked
        message_id = payload.message_id
        if message_id not in self.open_vote_sessions:
            return
        channel = self.bot.get_channel(payload.channel_id)
        assert isinstance(channel, GuildMessageable)
        message = await channel.fetch_message(message_id)

        vote_session = self.open_vote_sessions[message_id]
        # We should remove the reaction of all users except the bot, thus this should be placed before the trusted role check
        try:
            await message.remove_reaction(payload.emoji, user)
        except discord.Forbidden:
            pass

        # Check if the user has a trusted role
        trusted_role_ids = await get_server_setting(server_id=payload.guild_id, setting="Trusted")
        if trusted_role_ids is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        assert guild is not None
        member = guild.get_member(user.id)
        assert member is not None
        for role in member.roles:
            if role.id in trusted_role_ids:
                break
        else:
            await vote_session.message.channel.send("You do not have a trusted role.")
            return  # User does not have a trusted role

        original_vote = vote_session[user.id]
        if payload.emoji.name == APPROVE_EMOJI:
            vote_session[user.id] = 1 if original_vote != 1 else None
        elif payload.emoji.name == DENY_EMOJI:
            vote_session[user.id] = -1 if original_vote != -1 else None
        else:
            return

        # Update the embed
        await vote_session.update_message()

        # Check if the threshold has been met
        if vote_session.net_votes >= vote_session.pass_threshold:
            await vote_session.message.channel.send("Vote passed")
            if vote_session.target_message:
                try:
                    await vote_session.target_message.delete()
                    await vote_session.message.channel.send("Message deleted.")
                except discord.Forbidden:
                    await vote_session.message.channel.send("Bot lacks permissions to delete the message.")
                except discord.NotFound:
                    await vote_session.message.channel.send("The target message was not found.")
            del self.open_vote_sessions[message_id]


async def setup(bot: "RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    cog = DeleteLogCog(bot)
    open_vote_sessions = await DeleteLogVoteSession.get_open_vote_sessions(bot)
    cog.open_vote_sessions = {session.message.id: session for session in open_vote_sessions}

    await bot.add_cog(cog)
