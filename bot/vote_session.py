"""A vote session that represents a change to something."""

from __future__ import annotations

from collections.abc import Iterable
import inspect
from abc import abstractmethod, ABC
import asyncio
from asyncio import Task
from dataclasses import dataclass
from types import MethodType
from typing import Any, TypeVar, Union, TYPE_CHECKING, cast

import discord
from postgrest.base_request_builder import APIResponse

from bot import utils
from bot._types import GuildMessageable
from database import DatabaseManager
from database.builds import Build
from database.schema import VoteKind, MessageRecord
from database.vote import close_vote_session, track_vote_session, upsert_vote

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]


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

    def __init__(
        self,
        bot: discord.Client,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
    ):
        """
        Initialize the vote session, this should be called by subclasses only. Use create() instead.

        If you use this constructor directly, you must call _async_init() afterwards, or else the vote session will not be tracked.

        Args:
            bot: The discord client for fetching messages.
            messages: The messages (or their ids) belonging to the vote session.
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
        self.bot = bot
        self._messages: set[discord.Message]
        self.message_ids: set[int]
        if all(isinstance(message, int) for message in messages):
            messages = cast(list[int], messages)
            self._messages = set()
            self.message_ids = set(messages)
        else:
            messages = cast(list[discord.Message], messages)
            self._messages = set(messages)
            self.message_ids = set(message.id for message in messages)
        if len(messages) >= 10:
            raise ValueError(
                "Found a vote session with more than 10 messages, we need to change the update_message logic."
            )
        self.author_id = author_id
        self.pass_threshold = pass_threshold
        self.fail_threshold = fail_threshold
        self._votes: dict[int, float] = {}  # Dict of user_id: weight
        self._tasks: set[Task[Any]] = set()

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

    @abstractmethod
    async def _async_init(self) -> None:
        """Perform async initialization. Called by create()."""
        self.id = await track_vote_session(
            self._messages, self.author_id, self.kind, self.pass_threshold, self.fail_threshold
        )
        await self.update_messages()

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
    async def from_id(cls: type[T], bot: discord.Client, vote_session_id: int) -> Union[T, None]:
        """
        Create a vote session from an id.

        Args:
            bot: Required to fetch the actual message.
            vote_session_id: The id of the vote session.

        Returns:
            The vote session if it exists, otherwise None.
        """

    @property
    def upvotes(self) -> float:
        """Calculate the upvotes"""
        return sum(vote for vote in self._votes.values() if vote > 0)

    @property
    def downvotes(self) -> float:
        """Calculate the downvotes"""
        return -sum(vote for vote in self._votes.values() if vote < 0)

    @property
    def net_votes(self) -> float:
        """Calculate the net votes"""
        return sum(self._votes.values())

    @abstractmethod
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send a vote session message to a channel"""

    async def get_messages(self) -> set[discord.Message] | None:
        """Get the messages of the vote session if they exist in the cache"""
        if len(self.message_ids) == len(self._messages):
            return self._messages

    async def fetch_messages(self) -> set[discord.Message]:
        """Fetch the messages of the vote session"""
        if len(self.message_ids) == len(self._messages):
            return self._messages

        messages_record: APIResponse[MessageRecord] = (
            await DatabaseManager().table("messages").select("*").in_("message_id", self.message_ids).execute()
        )
        cached_ids = {message.id for message in self._messages}
        new_messages = await asyncio.gather(
            *(
                utils.getch(self.bot, record)
                for record in messages_record.data
                if record["message_id"] not in cached_ids
            )
        )
        new_messages = (message for message in new_messages if message is not None)
        self._messages.update(new_messages)
        assert len(self._messages) == len(self.message_ids)
        return self._messages

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

    def __getitem__(self, user_id: int) -> float | None:
        return self._votes.get(user_id)

    def __setitem__(self, user_id: int, weight: float | None) -> None:
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
            db_task = asyncio.create_task(upsert_vote(self.id, user_id, weight))
            self._tasks.add(db_task)
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
            await upsert_vote(self.id, user_id, weight)
