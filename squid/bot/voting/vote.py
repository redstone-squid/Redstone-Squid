"""Handles reaction-based voting for various purposes."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

import discord
from discord.ext.commands import Cog, Context, hybrid_command
from postgrest.base_request_builder import SingleAPIResponse

from squid.bot import utils
from squid.bot._types import GuildMessageable
from squid.bot.utils import is_staff
from squid.bot.voting.vote_session import AbstractVoteSession, BuildVoteSession, DeleteLogVoteSession

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]
# TODO: Unhardcode these emojis

logger = logging.getLogger(__name__)


class VoteCog[BotT: RedstoneSquid](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self._open_vote_sessions: dict[int, AbstractVoteSession] = {}

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
            .eq("message_id", message_id)
            .eq("purpose", "vote")
        )
        if status is not None:
            query.eq("vote_sessions(status)", status)
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

        if user.bot:
            return  # Ignore bot reactions

        # Update votes based on the reaction
        emoji_name = str(payload.emoji)
        user_id = payload.user_id

        if isinstance(vote_session, DeleteLogVoteSession):
            # Check if the user has a trusted role
            if payload.guild_id is None:
                raise NotImplementedError("Cannot vote in DMs.")

            trusted_role_ids = await self.bot.db.server_setting.get_single(
                server_id=payload.guild_id, setting="Trusted"
            )

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

        try:
            await remove_reaction_task
        except (discord.Forbidden, discord.NotFound):
            pass  # Ignore if we can't remove the reaction

    @hybrid_command(name="start_vote")
    async def start_vote(self, ctx: Context[BotT], target_message: discord.Message):
        """Starts a vote to delete a specified message by providing its URL."""
        # Check if guild_id matches the current guild
        if ctx.guild != target_message.guild:
            await ctx.send("The message is not from this guild.")
            return

        async with utils.RunningMessage(ctx) as message:
            vote_session = await DeleteLogVoteSession.create(
                self.bot, [message], author_id=ctx.author.id, target_message=target_message
            )
            self._open_vote_sessions[message.id] = vote_session


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    cog = VoteCog(bot)
    open_vote_sessions = await BuildVoteSession.get_open_vote_sessions(
        bot
    ) + await DeleteLogVoteSession.get_open_vote_sessions(bot)
    for session in open_vote_sessions:
        for message_id in session.message_ids:
            cog._open_vote_sessions[message_id] = session  # pyright: ignore [reportPrivateUsage]

    await bot.add_cog(cog)
