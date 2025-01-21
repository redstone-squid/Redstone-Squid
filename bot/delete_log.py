"""This module contains the DeleteLogCog class, which is a cog for voting to delete a message."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext.commands import command, Cog, Context

from bot import utils
from bot.voting.vote_session import DeleteLogVoteSession
from bot.utils import check_is_staff

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

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
            vote_session = await DeleteLogVoteSession.create(
                self.bot, [message], author_id=ctx.author.id, target_message=target_message
            )
            self.open_vote_sessions[message.id] = vote_session


async def setup(bot: "RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    cog = DeleteLogCog(bot)
    open_vote_sessions = await DeleteLogVoteSession.get_open_vote_sessions(bot)
    for session in open_vote_sessions:
        for message_id in session.message_ids:
            cog.open_vote_sessions[message_id] = session

    await bot.add_cog(cog)
