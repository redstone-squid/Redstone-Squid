from dataclasses import dataclass

import discord

from bot._types import GuildMessageable
from database.builds import Build

APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]


@dataclass
class Vote:
    """Represents a vote on a build."""

    guild: discord.Guild
    channel: GuildMessageable
    message: discord.Message
    build: Build
    user: discord.User


class VoteSessionBase:
    """A vote session that represents a change to a build."""

    def __init__(self, message: discord.Message, threshold: int = 7):
        self.message = message  # The message that shows the voting embed
        self.threshold = threshold  # Threshold for net upvotes
        self.votes: dict[int, int] = {}  # Dict of user_id: weight

    @property
    def upvotes(self) -> int:
        """Calculate the upvotes"""
        return sum(vote for user_id, vote in self.votes.items() if vote > 0)

    @property
    def downvotes(self) -> int:
        """Calculate the downvotes"""
        return sum(-vote for user_id, vote in self.votes.items() if vote < 0)

    @property
    def net_votes(self) -> int:
        """Calculate the net votes"""
        return sum(self.votes.values())

    async def update_embed(self, description: str = None):
        """Update the embed with new counts"""

        embed = self.message.embeds[0]
        embed.set_field_at(0, name="upvotes", value=str(self.upvotes), inline=True)
        embed.set_field_at(1, name="downvotes", value=str(self.downvotes), inline=True)
        await self.message.edit(embed=embed)
