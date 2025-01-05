"""A vote session that represents a change to something."""

from __future__ import annotations

import inspect
from abc import abstractmethod, ABC
import asyncio
from asyncio import Task
from dataclasses import dataclass
from types import MethodType
from typing import Any, TypeVar, Union, TYPE_CHECKING

import discord

from bot._types import GuildMessageable
from database.builds import Build
from database.schema import VoteKind
from database.vote import close_vote_session, track_vote_session, upsert_vote

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


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


T = TypeVar("T", bound="AbstractVoteSession")


class AbstractVoteSession(ABC):
    """
    A vote session that represents a change to something.

    Subclasses must implement the following methods:
    - _async_init()
    - create(), with the same signature as __init__
    - from_id()
    - update_message()
    - close()

    Subclasses must also set the kind attribute to a VoteKind.
    """

    kind: VoteKind

    def __init__(self, messages: list[discord.Message], author_id: int, pass_threshold: int, fail_threshold: int):
        """
        Initialize the vote session, this should be called by subclasses only. Use create() instead.

        If you use this constructor directly, you must call _async_init() afterwards, or else the vote session will not be tracked.

        Args:
            messages: The messages belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        self._allow_init: bool
        """A flag to allow direct initialization."""
        if getattr(self, "_allow_init", False) is not True:
            raise ValueError("Do not use __init__ directly, use create() instead.")

        super().__init__()
        self.id: int | None = None
        """The id of the vote session in the database. If None, we are not tracking the vote session and thus no async operations are performed."""
        self.is_closed = False
        self.messages = messages
        if len(messages) >= 10:
            raise ValueError(
                "Found a vote session with more than 10 messages, we need to change the update_message logic."
            )
        self.author_id = author_id
        self.pass_threshold = pass_threshold
        self.fail_threshold = fail_threshold
        self._votes: dict[int, int] = {}  # Dict of user_id: weight
        self._tasks: set[Task[Any]] = set()

    @abstractmethod
    async def _async_init(self) -> None:
        """Perform async initialization. Called by create()."""
        self.id = await track_vote_session(
            self.messages, self.author_id, self.kind, self.pass_threshold, self.fail_threshold
        )
        await self.update_messages()

    @classmethod
    @abstractmethod
    async def create(cls: type[T], *args, **kwargs) -> T:
        """
        Create and initialize a vote session. It should have the same signature as __init__.
        """
        self: T = cls.__new__(cls)
        self._allow_init = True
        self.__init__(*args, **kwargs)
        await self._async_init()
        return self

    def __init_subclass__(cls, **kwargs):
        """Check that the 'create' method signature matches the '__init__' method signature."""
        super().__init_subclass__(**kwargs)

        if inspect.isabstract(cls):
            return  # Skip abstract classes as their implementations are not yet fixed

        # Retrieve the __init__ method signature, excluding 'self'
        init_method = cls.__init__
        init_sig = inspect.signature(init_method)
        init_params = list(init_sig.parameters.values())[1:]  # Skip 'self'
        init_signature = inspect.Signature(parameters=init_params)

        # Retrieve the 'create' method
        create_method = getattr(cls, "create", None)
        if create_method is None:
            raise TypeError(f"Class '{cls.__name__}' must implement a 'create' method.")

        # Retrieve the underlying function from the classmethod
        assert isinstance(create_method, MethodType)  # For type checker
        create_func = create_method.__func__
        create_sig = inspect.signature(create_func)

        # Retrieve the 'create' method signature, excluding 'cls'
        create_params = list(create_sig.parameters.values())[1:]  # Skip 'cls'
        create_signature = inspect.Signature(parameters=create_params)

        # Compare signatures
        if init_signature != create_signature:
            raise TypeError(
                f"In class '{cls.__name__}', the 'create' method signature must match '__init__'.\n"
                f"__init__ signature: {init_signature}\n"
                f"create signature: {create_signature}"
            )

    @classmethod
    @abstractmethod
    async def from_id(cls: type[T], bot: RedstoneSquid, vote_session_id: int) -> Union[T, None]:
        """
        Create a vote session from an id.

        Args:
            bot: Required to fetch the actual message.
            vote_session_id: The id of the vote session.

        Returns:
            The vote session if it exists, otherwise None.
        """

    @property
    def upvotes(self) -> int:
        """Calculate the upvotes"""
        return sum(vote for vote in self._votes.values() if vote > 0)

    @property
    def downvotes(self) -> int:
        """Calculate the downvotes"""
        return -sum(vote for vote in self._votes.values() if vote < 0)

    @property
    def net_votes(self) -> int:
        """Calculate the net votes"""
        return sum(self._votes.values())

    @abstractmethod
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send a vote session message to a channel"""

    @abstractmethod
    async def update_messages(self) -> None:
        """Update the messages with an embed of new vote counts"""

    @abstractmethod
    async def close(self) -> None:
        """Close the vote session"""
        self.is_closed = True
        # Wait for any pending vote operations
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=False)
        await self.update_messages()
        assert self.id is not None
        await close_vote_session(self.id)

    def __getitem__(self, user_id: int) -> int | None:
        return self._votes.get(user_id)

    def __setitem__(self, user_id: int, weight: int | None) -> None:
        """
        Set a vote synchronously, creating background tasks for updates.
        For direct async access, use set_vote() instead.
        """
        if self.is_closed:
            raise ValueError("Cannot vote on a closed vote session.")

        if weight is None:
            self._votes.pop(user_id, None)
        else:
            self._votes[user_id] = weight

        if not self.fail_threshold < self.net_votes < self.pass_threshold:
            self._tasks.add(asyncio.create_task(self.close()))

        # Create tasks for the updates
        if self.id is not None:
            update_task = asyncio.create_task(self.update_messages())
            db_task = asyncio.create_task(upsert_vote(self.id, user_id, weight))
            self._tasks.add(update_task)
            self._tasks.add(db_task)

            # Remove tasks when they complete
            update_task.add_done_callback(self._tasks.discard)
            db_task.add_done_callback(self._tasks.discard)

    async def set_vote(self, user_id: int, weight: int | None) -> None:
        """Set a vote for a user with proper database tracking."""
        if self.is_closed:
            raise ValueError("Cannot vote on a closed vote session.")

        if weight is None:
            self._votes.pop(user_id, None)
        else:
            self._votes[user_id] = weight

        if not self.fail_threshold < self.net_votes < self.pass_threshold:
            await self.close()

        if self.id is not None:
            await asyncio.gather(self.update_messages(), upsert_vote(self.id, user_id, weight), return_exceptions=False)
