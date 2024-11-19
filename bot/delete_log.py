import discord
from discord.ext.commands import command, Cog, Context
from typing import TYPE_CHECKING, Dict, Optional
import re
from bot.vote_session import VoteSessionBase

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

APPROVE_EMOJI = "✅"
DENY_EMOJI = "❌"


class DeleteLogSession(VoteSessionBase):
    def __init__(
        self,
        message: discord.Message,
        target_message: Optional[discord.Message] = None,
        threshold: int = 1,
    ):
        super().__init__(message, threshold)
        self.target_message = target_message


class DeleteLogCog(Cog, name="Vote"):
    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot
        self.tracked_messages: Dict[int, DeleteLogSession] = {}

    @command(name="start_vote")
    async def start_vote(self, ctx: Context, message_url: Optional[str] = None):
        """Starts a vote. If a message URL is provided, starts a vote to delete that message."""
        if message_url:
            # Parse the message URL
            regex = r"https?://discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)"
            match = re.match(regex, message_url)
            if not match:
                await ctx.send("Invalid message URL.")
                return

            guild_id, channel_id, message_id = map(int, match.groups())
            # Check if guild_id matches the current guild
            if ctx.guild.id != guild_id:
                await ctx.send("The message is not from this guild.")
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                await ctx.send("Channel not found.")
                return

            try:
                target_message = await channel.fetch_message(message_id)
            except discord.NotFound:
                await ctx.send("Message not found.")
                return

            embed = discord.Embed(
                title="Vote to Delete Log",
                description=(
                    f"React with {APPROVE_EMOJI} to upvote or {DENY_EMOJI} to downvote.\n\n"
                    f"**Log Content:**\n{target_message.content}"
                ),
            )
            message = await ctx.send(embed=embed)
            # Add initial reactions
            await message.add_reaction(APPROVE_EMOJI)
            await message.add_reaction(DENY_EMOJI)
            self.tracked_messages[message.id] = DeleteLogSession(message, target_message=target_message)
        else:
            # Behavior without message URL
            embed = discord.Embed(
                title="Voting",
                description=f"React with {APPROVE_EMOJI} to upvote or {DENY_EMOJI} to downvote.",
            )
            message = await ctx.send(embed=embed)
            # Add initial reactions
            await message.add_reaction(APPROVE_EMOJI)
            await message.add_reaction(DENY_EMOJI)
            # Store the VoteSession for tracking
            self.tracked_messages[message.id] = DeleteLogSession(message)

    @Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """Handles reactions to update vote counts anonymously."""
        if user.bot:
            return  # Ignore bot reactions

        message_id = reaction.message.id

        # Check if the message is being tracked
        if message_id in self.tracked_messages:
            vote_session = self.tracked_messages[message_id]

            # Ensure the reaction is on the correct message
            if reaction.message.id != vote_session.message.id:
                return

            try:
                await reaction.remove(user)
            except discord.Forbidden:
                pass

            # Determine the vote type
            if str(reaction.emoji) == APPROVE_EMOJI:
                current_vote_set = vote_session.upvotes
                other_vote_set = vote_session.downvotes
            elif str(reaction.emoji) == DENY_EMOJI:
                current_vote_set = vote_session.downvotes
                other_vote_set = vote_session.upvotes
            else:
                return  # Ignore other reactions

            # Check if user has already voted with this reaction
            if user.id in current_vote_set:
                current_vote_set.discard(user.id)
            else:
                # Remove user from other vote set if they had voted before
                other_vote_set.discard(user.id)
                # Add user to current vote set
                current_vote_set.add(user.id)

            embed = vote_session.message.embeds[0]
            embed.description = (
                f"React with {APPROVE_EMOJI} to upvote or {DENY_EMOJI} to downvote.\n\n"
                f"**Upvotes:** {vote_session.approve_count}\n"
                f"**Downvotes:** {vote_session.deny_count}"
            )
            await vote_session.message.edit(embed=embed)

            if vote_session.net_votes() >= vote_session.threshold:
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
