"""This module contains the DeleteLogCog class, which is a cog for voting to delete a message."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import discord
from discord.ext.commands import command, Cog, Context

from bot import utils
from bot._types import GuildMessageable
from bot.voting.vote_session import DeleteLogVoteSession
from bot.utils import check_is_staff
from database.server_settings import get_server_setting

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

APPROVE_EMOJI = "✅"
DENY_EMOJI = "❌"


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
