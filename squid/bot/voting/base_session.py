"""Shared Discord vote-session behavior."""

import asyncio
import inspect
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from types import MethodType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, cast, final

import discord

from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    VoteChoice,
    VoteKindLiteral,
    VoteOption,
    VoteSessionResultLiteral,
    VoteSessionSnapshot,
    normalize_vote_options,
)

if TYPE_CHECKING:
    import squid.bot.app


class AbstractVoteSession(ABC):
    """
    A vote session that represents a change to something.

    Subclasses must implement the following methods:
    - _async_init()
    - create(), with the same signature as __init__
    - from_id()
    - update_message()
    Subclasses must also set the kind attribute to a VoteKind.
    """

    kind: ClassVar[VoteKindLiteral]

    def __init__(
        self,
        bot: "squid.bot.app.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ):
        """
        Initialize the vote session, this should be called by subclasses only. Use create() instead.

        If you use this constructor directly, you must call _async_init() afterwards, or else the vote session will not be tracked.

        Args:
            bot: The bot for fetching messages.
            messages: The messages (or their ids) belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        self._allow_init: bool
        """A flag to allow direct initialization."""
        if getattr(self, "_allow_init", False) is not True:
            msg = "Do not use __init__ directly, use create() instead."
            raise ValueError(msg)

        super().__init__()
        self.id: int | None = None
        """The id of the vote session in the database. If None, we are not tracking the vote session and thus no async operations are performed."""
        self.is_closed = False
        self.bot = bot
        self._messages: set[discord.Message]
        self.message_ids: set[int]
        self._message_channels: dict[int, int] = {}
        if all(isinstance(message, int) for message in messages):
            messages = cast(list[int], messages)
            self._messages = set()
            self.message_ids = set(messages)
        else:
            messages = cast(list[discord.Message], messages)
            self._messages = set(messages)
            self.message_ids = set(message.id for message in messages)
            self._message_channels = {message.id: message.channel.id for message in messages}
        if len(messages) >= 10:
            msg = "Found a vote session with more than 10 messages, we need to change the update_message logic."
            raise ValueError(msg)
        self.author_id = author_id
        self.pass_threshold = pass_threshold
        self.fail_threshold = fail_threshold
        self.options = normalize_vote_options(options)
        self._votes: dict[int, float] = {}  # Dict of user_id: weight

    @classmethod
    @abstractmethod
    async def create(cls: type[Self], *args: Any, **kwargs: Any) -> Self:
        """
        Create and initialize a vote session. It should have the same signature as __init__.
        """
        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(*args, **kwargs)
        await self._async_init()
        return self

    @abstractmethod
    async def _async_init(self) -> None:
        """Persist and initialize a newly created vote session."""

    def __init_subclass__(cls, **kwargs: Any):
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
            msg = f"Class '{cls.__name__}' must implement a 'create' method."
            raise TypeError(msg)

        # Retrieve the underlying function from the classmethod
        assert isinstance(create_method, MethodType)  # For type checker
        create_func = create_method.__func__
        create_sig = inspect.signature(create_func)

        # Retrieve the 'create' method signature, excluding 'cls'
        create_params = list(create_sig.parameters.values())[1:]  # Skip 'cls'
        create_signature = inspect.Signature(parameters=create_params)

        # Compare signatures
        if init_signature != create_signature:
            msg = (
                f"In class '{cls.__name__}', the 'create' method signature must match '__init__'.\n"
                f"__init__ signature: {init_signature}\n"
                f"create signature: {create_signature}"
            )
            raise TypeError(msg)

    @classmethod
    @abstractmethod
    async def from_id(cls: type[Self], bot: "squid.bot.app.RedstoneSquid", vote_session_id: int) -> Self | None:
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

    @final
    @property
    def status(self) -> Literal["open", "closed"]:
        """The current status of the vote session."""
        if self.is_closed:
            return "closed"
        return "open"

    @final
    @property
    def result(self) -> VoteSessionResultLiteral:
        """The current result of the vote session."""
        if self.is_closed:
            if self.net_votes >= self.pass_threshold:
                return "approved"
            if self.net_votes <= self.fail_threshold:
                return "denied"
            return "cancelled"
        return "pending"

    @final
    def primary_emoji(self, choice: VoteChoice) -> str:
        """Return the first configured emoji for a vote choice."""
        return next(option.emoji for option in self.options if option.choice is choice)

    @abstractmethod
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send a vote session message to a channel"""

    async def get_messages(self) -> set[discord.Message] | None:
        """Get the messages of the vote session if they exist in the cache"""
        if len(self.message_ids) == len(self._messages):
            return self._messages
        return None

    async def fetch_messages(self) -> set[discord.Message]:
        """Fetch all messages for this vote session."""
        if len(self.message_ids) == len(self._messages):
            return self._messages

        cached_ids = {message.id for message in self._messages}
        missing = [
            (message_id, self._message_channels[message_id])
            for message_id in self.message_ids - cached_ids
            if message_id in self._message_channels
        ]
        new_messages = await asyncio.gather(
            *(self.bot.get_or_fetch_message(channel_id, message_id) for message_id, channel_id in missing)
        )
        self._messages.update(message for message in new_messages if message is not None)
        assert len(self._messages) == len(self.message_ids)
        return self._messages

    @abstractmethod
    async def update_messages(self) -> None:
        """Update the messages with an embed of new vote counts"""

    def __getitem__(self, user_id: int) -> float | None:
        return self._votes.get(user_id)

    def apply_persisted_state(self, snapshot: VoteSessionSnapshot) -> None:
        """Apply state returned by the atomic voting service before rendering."""
        if self.id != snapshot.id:
            msg = f"Cannot apply vote session {snapshot.id} to session {self.id}."
            raise ValueError(msg)
        self._votes = dict(snapshot.votes)
        self.is_closed = snapshot.status == "closed"
        self.options = snapshot.options
