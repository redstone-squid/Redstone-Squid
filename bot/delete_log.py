"""This module contains the DeleteLogCog class, which is a cog for voting to delete a message."""

import asyncio
from collections.abc import Iterable, Mapping
from textwrap import dedent

import discord
from discord.ext.commands import command, Cog, Context
from typing import TYPE_CHECKING, Any, final, cast

from postgrest.base_request_builder import SingleAPIResponse
from typing_extensions import override

from bot import utils
from bot._types import GuildMessageable
from database.voting.vote_session import AbstractVoteSession
from bot.utils import check_is_staff
from database.schema import VoteSessionRecord
from database.server_settings import get_server_setting
from database.voting.vote import track_vote_session, track_delete_log_vote_session, close_vote_session

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
        bot: RedstoneSquid,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ):
        """
        Initialize the delete log vote session.

        Args:
            bot: The discord client.
            messages: The messages (or their ids) belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            target_message: The message to delete if the vote passes.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold)
        self.target_message = target_message

    @classmethod
    @override
    async def create(
        cls,
        bot: RedstoneSquid,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> "DeleteLogVoteSession":
        self = await super().create(bot, messages, author_id, target_message, pass_threshold, fail_threshold)
        assert isinstance(self, DeleteLogVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        self.id = await track_vote_session(
            await self.fetch_messages(), self.author_id, self.kind, self.pass_threshold, self.fail_threshold
        )
        await track_delete_log_vote_session(self.id, self.target_message)
        await self.update_messages()

    @classmethod
    @override
    async def from_id(cls, bot: RedstoneSquid, vote_session_id: int) -> "DeleteLogVoteSession | None":
        vote_session_response: SingleAPIResponse[dict[str, Any]] | None = (
            await bot.db.table("vote_sessions")
            .select("*, messages(*), votes(*), delete_log_vote_sessions(*)")
            .eq("id", vote_session_id)
            .eq("kind", cls.kind)
            .maybe_single()
            .execute()
        )
        if vote_session_response is None:
            return None

        vote_session_record = vote_session_response.data
        target_message = await utils.getch(bot, vote_session_record["delete_log_vote_sessions"])
        if target_message is None:
            return None

        return await cls._from_record(bot, vote_session_record)

    @classmethod
    async def _from_record(cls, bot: RedstoneSquid, record: Mapping[str, Any]) -> "DeleteLogVoteSession | None":
        """Create a DeleteLogVoteSession from a database record."""
        target_message = await utils.getch(bot, record["delete_log_vote_sessions"])
        if target_message is None:
            return None

        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            bot,
            [msg["message_id"] for msg in record["messages"]],
            record["author_id"],
            target_message,
            record["pass_threshold"],
            record["fail_threshold"],
        )
        self.id = record["id"]  # We can skip _async_init because we already have the id and everything has been tracked before
        self._votes = {vote["user_id"]: vote["weight"] for vote in record["votes"]}
        return self

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial message to the channel."""
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
        return await channel.send(embed=embed)

    @override
    async def update_messages(self) -> None:
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
        await asyncio.gather(*[message.edit(embed=embed) for message in await self.fetch_messages()])

    @override
    async def close(self) -> None:
        if self.is_closed:
            return

        self.is_closed = True
        if self.net_votes <= self.pass_threshold:
            await asyncio.gather(*[message.channel.send("Vote failed") for message in await self.fetch_messages()])
        else:
            await self.target_message.delete()

        if self.id is not None:
            await close_vote_session(self.id)

    @classmethod
    async def get_open_vote_sessions(
        cls: type["DeleteLogVoteSession"], bot: RedstoneSquid
    ) -> list["DeleteLogVoteSession"]:
        """Get all open vote sessions from the database."""
        records: list[VoteSessionRecord] = (
            await bot.db.table("vote_sessions")
            .select("*, messages(*), votes(*), delete_log_vote_sessions(*)")
            .eq("status", "open")
            .eq("kind", cls.kind)
            .execute()
        ).data

        sessions = await asyncio.gather(*[cls._from_record(bot, record) for record in records])
        return [session for session in sessions if session is not None]


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
            vote_session = await DeleteLogVoteSession.create(
                self.bot, [message], author_id=ctx.author.id, target_message=target_message
            )
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
        channel = cast(GuildMessageable, self.bot.get_channel(payload.channel_id))
        message = await channel.fetch_message(message_id)

        vote_session = self.open_vote_sessions[message_id]
        # We should remove the reaction of all users except the bot, thus this should be placed before the trusted role check
        try:
            await message.remove_reaction(payload.emoji, user)
        except discord.Forbidden:
            pass

        # Check if the user has a trusted role
        trusted_role_ids = await get_server_setting(server_id=payload.guild_id, setting="Trusted")

        guild = self.bot.get_guild(payload.guild_id)
        assert guild is not None
        member = guild.get_member(user.id)
        assert member is not None
        for role in member.roles:
            if role.id in trusted_role_ids:
                break
        else:
            await channel.send("You do not have a trusted role.")
            return  # User does not have a trusted role

        original_vote = vote_session[user.id]
        if payload.emoji.name == APPROVE_EMOJI:
            vote_session[user.id] = 1 if original_vote != 1 else None
        elif payload.emoji.name == DENY_EMOJI:
            vote_session[user.id] = -1 if original_vote != -1 else None
        else:
            return

        # Update the embed
        await vote_session.update_messages()

        # Check if the threshold has been met
        if vote_session.net_votes >= vote_session.pass_threshold:
            if vote_session.target_message:
                try:
                    await vote_session.target_message.delete()
                except discord.Forbidden:
                    pass
                except discord.NotFound:
                    pass
            del self.open_vote_sessions[message_id]


async def setup(bot: "RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    cog = DeleteLogCog(bot)
    open_vote_sessions = await DeleteLogVoteSession.get_open_vote_sessions(bot)
    for session in open_vote_sessions:
        for message_id in session.message_ids:
            cog.open_vote_sessions[message_id] = session

    await bot.add_cog(cog)
