from typing import Set
import discord

APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]


class VoteSessionBase:
    def __init__(self, message: discord.Message, threshold: int = 7):
        self.message = message  # The message that shows the voting embed
        self.threshold = threshold  # Threshold for net upvotes
        self.upvotes: Set[int] = set()  # User IDs who upvoted
        self.downvotes: Set[int] = set()  # User IDs who downvoted

    @property
    def approve_count(self) -> int:
        return len(self.upvotes)

    @property
    def deny_count(self) -> int:
        return len(self.downvotes)

    def net_votes(self) -> int:
        return self.approve_count - self.deny_count

    async def update_embed(self, description: str = None):
        # Update the embed with new counts
        embed = self.message.embeds[0]
        if description:
            embed.description = description
        embed.set_field_at(0, name="upvotes", value=str(self.approve_count), inline=True)
        embed.set_field_at(1, name="downvotes", value=str(self.deny_count), inline=True)
        await self.message.edit(embed=embed)

    async def remove_user_reaction(self, user: discord.User, emojis: list):
        """Helper method to remove specific reactions from a user."""
        for emoji in emojis:
            for reaction in self.message.reactions:
                if str(reaction.emoji) == emoji:
                    await reaction.remove(user)
                    break
