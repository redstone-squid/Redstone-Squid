"""Handles reaction-based voting for various purposes."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import discord
from discord.ext.commands import Cog, Context, hybrid_command

from squid.bot.services.vote import DiscordDeleteLogVoteSession
from squid.db.vote_session import get_vote_session

if TYPE_CHECKING:
    import squid.bot


logger = logging.getLogger(__name__)


class VoteCog[BotT: "squid.bot.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @Cog.listener(name="on_raw_reaction_add")
    async def update_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        # Prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        vote_session = await get_vote_session(self.bot, payload.message_id, status="open")
        if vote_session is None:
            return
        await vote_session.on_raw_reaction_add(payload)

    @Cog.listener(name="on_raw_reaction_remove")
    async def remove_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reaction removal to update vote counts."""
        # Prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        vote_session = await get_vote_session(self.bot, payload.message_id, status="open")
        if vote_session is None:
            return
        await vote_session.on_raw_reaction_remove(payload)

    @hybrid_command(name="start_vote")
    async def start_vote(self, ctx: Context[BotT], target_message: discord.Message):
        """Starts a vote to delete a specified message by providing its URL."""
        # Check if guild_id matches the current guild
        if ctx.guild != target_message.guild:
            await ctx.send("The message is not from this guild.")
            return

        # Using the running message directly as a message to display the vote session
        async with self.bot.get_running_message(ctx) as message:
            await DiscordDeleteLogVoteSession.create(
                self.bot,
                [message],
                author_id=ctx.author.id,
                target_message=target_message,
                pass_threshold=3,
                fail_threshold=-3,
                approve_emojis=self.bot.default_approve_emojis,
                deny_emojis=self.bot.default_deny_emojis,
            )


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VoteCog(bot))
