"""Handles reaction-based voting for various purposes."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, cast

import discord
from discord.ext.commands import Cog, Context, hybrid_command

from squid.bot._types import GuildMessageable
from squid.bot.utils.permissions import is_staff, is_trusted_or_staff
from squid.bot.voting.vote_session import AbstractVoteSession, DeleteLogVoteSession, get_vote_session

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

    async def get_vote_session_messages(self, vote_session: AbstractVoteSession) -> AsyncGenerator[discord.Message, None]:
        """Get the messages associated with a vote session."""
        messages = await self.bot.db.message.get_messages_by_id(vote_session.message_ids)
        discord_message_tasks = [
            asyncio.create_task(self.bot.get_or_fetch_message(message.id, channel_id=message.channel_id))
            for message in messages
        ]
        for task in asyncio.as_completed(discord_message_tasks):
            msg = await task
            if msg is not None:
                yield msg

    @Cog.listener(name="on_raw_reaction_add")
    async def update_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        # This must be before the removal of the reaction to prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        vote_session = await get_vote_session(payload.message_id, status="open")
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
