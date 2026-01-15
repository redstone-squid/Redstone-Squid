"""Handles reaction-based voting for various purposes."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

import discord
from discord.ext.commands import Cog, Context, hybrid_command
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from squid.bot._types import GuildMessageable
from squid.bot.utils.permissions import is_staff, is_trusted_or_staff
from squid.bot.voting.vote_session import AbstractVoteSession, BuildVoteSession, DeleteLogVoteSession
from squid.db.schema import Message, VoteSession

if TYPE_CHECKING:
    import squid.bot


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]
# TODO: Unhardcode these emojis

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()


class VoteCog[BotT: "squid.bot.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self._background_tasks: set[asyncio.Task[Any]] = set()

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
        async with self.bot.db.async_session() as session:
            stmt = (
                select(Message)
                .options(selectinload(Message.vote_session))
                .where(Message.id == message_id, Message.purpose == "vote")
            )
            if status is not None:
                stmt = stmt.where(Message.vote_session.has(VoteSession.status == status))

            result = await session.execute(stmt)
            message = result.scalar_one_or_none()

            if message is None or message.vote_session is None:
                return None

            vote_session_id = message.vote_session_id
            assert vote_session_id is not None, (
                "Vote session ID should not be None because we selected messages with the vote purpose."
            )
            kind = message.vote_session.kind

            if kind == "build":
                return await BuildVoteSession.from_id(self.bot, vote_session_id)
            if kind == "delete_log":
                return await DeleteLogVoteSession.from_id(self.bot, vote_session_id)
            logger.error("Unknown vote session kind: %s", kind)
            raise NotImplementedError(f"Unknown vote session kind: {kind}")

    @Cog.listener(name="on_raw_reaction_add")
    async def update_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        # This must be before the removal of the reaction to prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        vote_session = await self.get_vote_session(payload.message_id, status="open")
        if vote_session is None:
            return

        # Remove the user's reaction to keep votes anonymous
        channel = cast(GuildMessageable, self.bot.get_channel(payload.channel_id))
        message = await channel.fetch_message(payload.message_id)
        user = self.bot.get_user(payload.user_id)
        assert user is not None
        remove_reaction_task = asyncio.create_task(message.remove_reaction(payload.emoji, user))
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
            await DeleteLogVoteSession.create(
                self.bot, [message], author_id=ctx.author.id, target_message=target_message
            )


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VoteCog(bot))
