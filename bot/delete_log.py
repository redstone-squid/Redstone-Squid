import asyncio
from textwrap import dedent

import discord
from discord.ext.commands import command, Cog, Context
from typing import TYPE_CHECKING

from typing_extensions import override

from bot import utils
from bot.vote_session import AbstractVoteSession
from bot.utils import check_is_staff
from database.server_settings import get_server_setting

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

APPROVE_EMOJI = "✅"
DENY_EMOJI = "❌"


class DeleteLogVoteSession(AbstractVoteSession):
    """A vote session for deleting a message."""

    def __init__(
        self,
        message: discord.Message,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> None:
        """
        Initializes the vote session.

        Args:
            message: The message to track votes on.
            target_message: The message to delete if the vote passes.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        self.target_message = target_message
        super().__init__(message, pass_threshold, fail_threshold)

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


class DeleteLogCog(Cog, name="Vote"):
    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot
        self.tracked_messages: dict[int, DeleteLogVoteSession] = {}

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
            vote_session = DeleteLogVoteSession(message, target_message=target_message)
            self.tracked_messages[message.id] = vote_session

    @Cog.listener("on_reaction_add")
    async def update_delete_log_vote_sessions(self, reaction: discord.Reaction, user: discord.User):
        """Handles reactions to update vote counts anonymously."""
        if user.bot:
            return  # Ignore bot reactions

        if (guild := reaction.message.guild) is None:
            return

        # Check if the message is being tracked
        message_id = reaction.message.id
        if message_id not in self.tracked_messages:
            return

        vote_session = self.tracked_messages[message_id]
        # We should remove the reaction of all users except the bot, thus this should be placed before the trusted role check
        try:
            await reaction.remove(user)
        except discord.Forbidden:
            pass

        # Check if the user has a trusted role
        trusted_role_ids = await get_server_setting(server_id=guild.id, setting="Trusted")
        if trusted_role_ids is None:
            return

        member = guild.get_member(user.id)
        assert member is not None
        for role in member.roles:
            if role.id in trusted_role_ids:
                break
        else:
            await vote_session.message.channel.send("You do not have a trusted role.")
            return  # User does not have a trusted role

        original_vote = vote_session.votes.get(user.id, 0)
        if reaction.emoji == APPROVE_EMOJI:
            vote_session.votes[user.id] = 1 if original_vote != 1 else 0
        elif reaction.emoji == DENY_EMOJI:
            vote_session.votes[user.id] = -1 if original_vote != -1 else 0
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
            del self.tracked_messages[message_id]


async def setup(bot: "RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(DeleteLogCog(bot))
