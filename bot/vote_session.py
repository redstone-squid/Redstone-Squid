from abc import abstractmethod, ABC
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


class AbstractVoteSession(ABC):
    """A vote session that represents a change to something."""

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

    @abstractmethod
    async def update_message(self):
        """Update the message with an embed with new counts"""
