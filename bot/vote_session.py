import asyncio
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

    def __init__(self, message: discord.Message, pass_threshold: int = 7, fail_threshold: int = 7):
        """
        Initialize the vote session.

        Args:
            message: The message to track votes on.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        self.message = message  # The message that shows the voting embed
        self.pass_threshold = pass_threshold
        self.fail_threshold = fail_threshold
        self.votes: dict[int, int] = {}  # Dict of user_id: weight
        self.background_tasks = set()
        self.background_tasks.add(asyncio.create_task(self.update_message()))

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
