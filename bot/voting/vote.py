from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord
from discord.ext import commands
from discord.ext.commands import Cog

from bot.voting.vote_session import AbstractVoteSession, BuildVoteSession, DeleteLogVoteSession
from bot.utils import is_staff
from bot._types import GuildMessageable
from database.server_settings import get_server_setting

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]
# TODO: Unhardcode these emojis


class VoteCog(Cog):

    def __init__(self, bot: RedstoneSquid):
        self.bot = bot
        self.open_vote_sessions: dict[int, AbstractVoteSession] = {}

    async def get_voting_weight(self, server_id: int | None, user_id: int) -> float:
        """Get the voting weight of a user."""
        if await is_staff(self.bot, server_id, user_id):
            return 3
        return 1

    @Cog.listener(name="on_raw_reaction_add")
    async def update_vote_sessions(self, payload: discord.RawReactionActionEvent):
        """Handles reactions to update vote counts anonymously."""
        if payload.guild_id is None:
            raise NotImplementedError("Cannot vote in DMs.")

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

        if user.bot:
            return  # Ignore bot reactions

        # Update votes based on the reaction
        emoji_name = str(payload.emoji)
        user_id = payload.user_id

        if isinstance(vote_session, DeleteLogVoteSession):
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


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    cog = VoteCog(bot)
    open_vote_sessions = (
        await BuildVoteSession.get_open_vote_sessions(bot)
        + await DeleteLogVoteSession.get_open_vote_sessions(bot)
    )
    for session in open_vote_sessions:
        for message_id in session.message_ids:
            cog.open_vote_sessions[message_id] = session

    await bot.add_cog(cog)
