from abc import abstractmethod, ABC
from dataclasses import dataclass
from typing import Any

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

    def __init__(self, message: discord.Message, threshold: int = 7, negative_threshold: int = 7):
        """
        Initialize the vote session.

        Args:
            message: The message to track votes on.
            threshold: The number of votes required to pass the vote.
            negative_threshold: The number of votes required to fail the vote.
        """
        self.message = message  # The message that shows the voting embed
        self.threshold = threshold
        self.negative_threshold = negative_threshold
        self.votes: dict[int, int] = {}  # Dict of user_id: weight

    @classmethod
    @abstractmethod
    async def create(cls, *args: Any, **kwargs: Any) -> "AbstractVoteSession":
        """
        Initialize a vote session.

        Actually the correct signature is `async def create(cls, message: discord.Message, *args: Any, **kwargs: Any)`,
        but static type checkers will complain about incompatible overrides if we add it.

        A standard implementation would look like this:
        ```python
        self = cls(message, target_message, threshold, negative_threshold)
        await self.update_message()
        return self
        ```
        However, we intentionally leave this method abstract to force subclasses to implement it and match the signature to their `__init__` method.
        """

    @property
    def upvotes(self) -> int:
        """Calculate the upvotes"""
        return sum(vote for vote in self.votes.values() if vote > 0)

    @property
    def downvotes(self) -> int:
        """Calculate the downvotes"""
        return -sum(vote for vote in self.votes.values() if vote < 0)

    @property
    def net_votes(self) -> int:
        """Calculate the net votes"""
        return sum(self.votes.values())

    @abstractmethod
    async def update_message(self):
        """Update the message with an embed with new counts"""
