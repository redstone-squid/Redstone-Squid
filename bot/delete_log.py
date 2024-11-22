import discord
from discord.ext.commands import command, Cog, Context
from typing import TYPE_CHECKING, Dict, Optional
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
        threshold: int = 7,
    ):
        super().__init__(message, threshold)
        self.target_message = target_message


class DeleteLogCog(Cog, name="Vote"):
    def __init__(self, bot: "RedstoneSquid"):
        self.bot = bot
        self.tracked_messages: Dict[int, DeleteLogSession] = {}

    @command(name="start_vote")
    async def start_vote(self, ctx: Context, target_message: Optional[discord.Message] = None):
        """Starts a vote to delete a specified message by providing its URL."""
        if target_message:
            # Check if guild_id matches the current guild
            if ctx.guild != target_message.guild:
                await ctx.send("The message is not from this guild.")
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
            await ctx.send("Must provide a log to delete")
            return

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

            original_vote = vote_session.votes.get(user.id, 0)
            if reaction.emoji == APPROVE_EMOJI:
                vote_session.votes[user.id] = 1 if original_vote != 1 else 0
            elif reaction.emoji == DENY_EMOJI:
                vote_session.votes[user.id] = -1 if original_vote != -1 else 0
            else:
                return

            # Update the embed
            embed = vote_session.message.embeds[0]
            embed.description = (
                f"React with {APPROVE_EMOJI} to upvote or {DENY_EMOJI} to downvote.\n\n"
                f"**Upvotes:** {vote_session.upvotes}\n"
                f"**Downvotes:** {vote_session.downvotes}"
            )
            await vote_session.message.edit(embed=embed)

            # Check if the threshold has been met
            if vote_session.net_votes >= vote_session.threshold:
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
