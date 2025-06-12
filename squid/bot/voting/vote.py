"""Handles reaction-based voting for various purposes."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

import discord
from discord.ext.commands import Cog, Context, hybrid_command
from postgrest.base_request_builder import SingleAPIResponse

from squid.bot._types import GuildMessageable
from squid.bot.utils.permissions import is_staff, is_trusted_or_staff
from squid.bot.voting.vote_session import AbstractVoteSession, BuildVoteSession, DeleteLogVoteSession

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]
# TODO: Unhardcode these emojis

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()


class VoteCog[BotT: RedstoneSquid](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self._open_vote_sessions: dict[int, AbstractVoteSession] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def load_vote_sessions(self):
        """Load open vote sessions from the database."""
        try:
            open_vote_sessions = await BuildVoteSession.get_open_vote_sessions(
                self.bot
            ) + await DeleteLogVoteSession.get_open_vote_sessions(self.bot)
            for session in open_vote_sessions:
                for message_id in session.message_ids:
                    self._open_vote_sessions[message_id] = session
        except Exception as e:
            logger.error(f"Failed to load open vote sessions: {e}")

    async def get_voting_weight(self, server_id: int | None, user_id: int) -> float:
        """Get the voting weight of a user."""
        if await is_staff(self.bot, server_id, user_id):
            return 3
        return 1

    async def get_vote_session(
        self, message_id: int, *, status: Literal["open", "closed"] | None = None
    ) -> AbstractVoteSession | None:
        """Gets a vote session from the database.

        Args:
            message_id: The message ID of the vote session.
            status: The status of the vote session. If None, it will get any status.
        """

        query = (
            self.bot.db.table("messages")
            .select("vote_session_id, vote_sessions(kind)")
            .eq("id", message_id)
            .eq("purpose", "vote")
        )
        if status is not None:
            query.eq("vote_sessions.status", status)
        response: SingleAPIResponse[dict[str, Any]] | None = await query.maybe_single().execute()

        if response is None:
            return None

        vote_session_id: int = response.data["vote_session_id"]
        kind: str = response.data["vote_sessions"]["kind"]

        if kind == "build":
            return await BuildVoteSession.from_id(self.bot, vote_session_id)
        elif kind == "delete_log":
            return await DeleteLogVoteSession.from_id(self.bot, vote_session_id)
        else:
            logger.error(f"Unknown vote session kind: {kind}")
            raise NotImplementedError(f"Unknown vote session kind: {kind}")

    @Cog.listener(name="on_raw_reaction_add")
    async def update_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        # This must be before the removal of the reaction to prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        if (vote_session := self._open_vote_sessions.get(payload.message_id)) is None:
            vote_session = await self.get_vote_session(payload.message_id, status="open")
            if vote_session is None:
                return

        if vote_session.is_closed:
            for message_id in vote_session.message_ids:
                self._open_vote_sessions.pop(message_id, None)

        # Remove the user's reaction to keep votes anonymous
        channel = cast(GuildMessageable, self.bot.get_channel(payload.channel_id))
        message = await channel.fetch_message(payload.message_id)
        user = self.bot.get_user(payload.user_id)
        assert user is not None
        remove_reaction_task = asyncio.create_task(
            message.remove_reaction(payload.emoji, user)
        )  # await later as this is not critical
        self._background_tasks.add(remove_reaction_task)
        remove_reaction_task.add_done_callback(self._background_tasks.discard)

        if user.bot:
            return  # Ignore bot reactions

        # Update votes based on the reaction
        emoji_name = str(payload.emoji)
        user_id = payload.user_id

        if isinstance(vote_session, DeleteLogVoteSession):
            # Check if the user has a trusted role
            if payload.guild_id is None:
                raise NotImplementedError("Cannot vote in DMs.")

            if await is_trusted_or_staff(self.bot, payload.guild_id, user_id):
                pass
            else:
                await channel.send("You do not have a trusted role.")
                return

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

    @hybrid_command(name="start_vote")
    async def start_vote(self, ctx: Context[BotT], target_message: discord.Message):
        """Starts a vote to delete a specified message by providing its URL."""
        # Check if guild_id matches the current guild
        if ctx.guild != target_message.guild:
            await ctx.send("The message is not from this guild.")
            return

        async with self.bot.get_running_message(ctx) as message:
            vote_session = await DeleteLogVoteSession.create(
                self.bot, [message], author_id=ctx.author.id, target_message=target_message
            )
            self._open_vote_sessions[message.id] = vote_session


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    cog = VoteCog(bot)
    # Load open vote sessions in the background because it can take a while
    # and this cog does not need to wait for it to finish
    load_task = asyncio.create_task(cog.load_vote_sessions())
    _background_tasks.add(load_task)
    load_task.add_done_callback(_background_tasks.discard)
    await bot.add_cog(cog)
