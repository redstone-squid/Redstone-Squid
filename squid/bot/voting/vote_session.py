"""A vote session that represents a change to something."""

import asyncio
import contextlib
import inspect
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from textwrap import dedent
from types import MethodType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, cast, final, override

import discord

from squid.bot.message_adapter import to_tracked_message
from squid.db.builds import Build
from squid.db.schema import Status
from squid.messages.application import MessageService
from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    VoteChange,
    VoteChoice,
    VoteKindLiteral,
    VoteOption,
    VoteSessionResultLiteral,
    VoteSessionSnapshot,
    normalize_vote_options,
)

if TYPE_CHECKING:
    import squid.bot


async def track_vote_messages(
    messages: Iterable[discord.Message],
    message_service: MessageService,
    vote_session_id: int,
    *,
    build_id: int | None = None,
) -> None:
    """Associate Discord messages with a persisted vote session.

    Args:
        messages: The messages belonging to the vote session.
        message_service: Application service for tracked Discord messages.
        vote_session_id: The persisted vote session identifier.
        build_id: The id of the build to vote on. None if the vote is not about a build.
    """
    coros = [
        message_service.track(
            to_tracked_message(message),
            "vote",
            build_id=build_id,
            vote_session_id=vote_session_id,
        )
        for message in messages
    ]
    await asyncio.gather(*coros)


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
        bot: "squid.bot.RedstoneSquid",
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
    async def from_id(cls: type[Self], bot: "squid.bot.RedstoneSquid", vote_session_id: int) -> Self | None:
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


@final
class BuildVoteSession(AbstractVoteSession):
    """A vote session for a confirming or denying a build."""

    kind = "build"

    def __init__(
        self,
        bot: "squid.bot.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ):
        """
        Initialize the vote session.

        Args:
            bot: The discord bot.
            messages: The messages belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            build: The build which the vote session is for. If type is "update", this is the updated build.
            type: Whether to add or update the build.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold, options)
        self.build = build
        self.type = type

    @classmethod
    @override
    async def create(
        cls,
        bot: "squid.bot.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> "BuildVoteSession":
        self = await super().create(bot, messages, author_id, build, type, pass_threshold, fail_threshold, options)
        assert isinstance(self, BuildVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        assert self.build.id is not None
        if self.type == "add":
            changes: list[VoteChange] = [("submission_status", Status.PENDING, Status.CONFIRMED)]
        else:
            original = await self.bot.services.builds.get(self.build.id)
            assert original is not None
            changes = original.diff(self.build)

        self.id = await self.bot.services.votes.start_build_vote(
            author_id=self.author_id,
            pass_threshold=self.pass_threshold,
            fail_threshold=self.fail_threshold,
            build_id=self.build.id,
            changes=changes,
            options=self.options,
        )
        await track_vote_messages(
            await self.fetch_messages(),
            self.bot.services.messages,
            self.id,
            build_id=self.build.id,
        )

        await self.update_messages()

        reaction_tasks = [
            message.add_reaction(self.primary_emoji(choice))
            for message in self._messages
            for choice in (VoteChoice.APPROVE, VoteChoice.DENY)
        ]
        with contextlib.suppress(discord.Forbidden):
            await asyncio.gather(*reaction_tasks)  # Bot doesn't have permission to add reactions

    @classmethod
    @override
    async def from_id(cls, bot: "squid.bot.RedstoneSquid", vote_session_id: int) -> "BuildVoteSession | None":
        snapshot = await bot.services.votes.get_session_by_id(vote_session_id)
        if snapshot is None or snapshot.kind != cls.kind:
            return None
        return await cls._from_snapshot(bot, snapshot)

    @classmethod
    async def _from_snapshot(
        cls, bot: "squid.bot.RedstoneSquid", snapshot: VoteSessionSnapshot
    ) -> "BuildVoteSession | None":
        """Restore a Discord view from an application snapshot."""
        if snapshot.target.build_id is None:
            msg = f"Found a build vote session with no associated build id. session_id={snapshot.id}"
            raise ValueError(msg)

        build = await bot.services.builds.get(snapshot.target.build_id)
        if build is None:
            return None
        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            bot=bot,
            messages=snapshot.message_ids,
            author_id=snapshot.author_id,
            build=build,
            type="add",  # TODO: Handle update type properly
            pass_threshold=snapshot.pass_threshold,
            fail_threshold=snapshot.fail_threshold,
            options=snapshot.options,
        )
        self.id = snapshot.id
        self._message_channels = {message.id: message.channel_id for message in snapshot.messages}
        self.apply_persisted_state(snapshot)
        return self

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        message = await channel.send(
            content=self.build.original_link, embed=await self.bot.for_build(self.build).generate_embed()
        )
        await self.bot.services.messages.track(
            to_tracked_message(message),
            purpose="vote",
            build_id=self.build.id,
            vote_session_id=self.id,
        )
        self._messages.add(message)
        return message

    @override
    async def update_messages(self):
        embed = await self.bot.for_build(self.build).generate_embed()
        embed.add_field(name="", value="", inline=False)  # Add a blank field to separate the vote count
        embed.add_field(name="Accept", value=f"{self.upvotes}/{self.pass_threshold}", inline=True)
        embed.add_field(name="Deny", value=f"{self.downvotes}/{-self.fail_threshold}", inline=True)
        await asyncio.gather(
            *[message.edit(content=self.build.original_link, embed=embed) for message in await self.fetch_messages()]
        )

    @classmethod
    async def get_open_vote_sessions(
        cls: type["BuildVoteSession"], bot: "squid.bot.RedstoneSquid"
    ) -> "list[BuildVoteSession]":
        """Get all open vote sessions from the database."""
        sessions = await asyncio.gather(
            *(cls._from_snapshot(bot, snapshot) for snapshot in await bot.services.votes.list_open(cls.kind))
        )
        return [session for session in sessions if session is not None]


@final
class DeleteLogVoteSession(AbstractVoteSession):
    """A vote session for deleting a message from the log."""

    kind = "delete_log"

    def __init__(
        self,
        bot: "squid.bot.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ):
        """
        Initialize the delete log vote session.

        Args:
            bot: The discord client.
            messages: The messages (or their ids) belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            target_message: The message to delete if the vote passes.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
        """
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold, options)
        self.target_message = target_message

    @classmethod
    @override
    async def create(
        cls,
        bot: "squid.bot.RedstoneSquid",
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> "DeleteLogVoteSession":
        self = await super().create(bot, messages, author_id, target_message, pass_threshold, fail_threshold, options)
        assert isinstance(self, DeleteLogVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        assert self.target_message.guild is not None
        self.id = await self.bot.services.votes.start_delete_log_vote(
            author_id=self.author_id,
            pass_threshold=self.pass_threshold,
            fail_threshold=self.fail_threshold,
            message_id=self.target_message.id,
            channel_id=self.target_message.channel.id,
            server_id=self.target_message.guild.id,
            options=self.options,
        )
        await track_vote_messages(
            await self.fetch_messages(),
            self.bot.services.messages,
            self.id,
        )
        await self.update_messages()
        reaction_tasks = [
            message.add_reaction(self.primary_emoji(choice))
            for message in self._messages
            for choice in (VoteChoice.APPROVE, VoteChoice.DENY)
        ]
        with contextlib.suppress(discord.Forbidden):
            await asyncio.gather(*reaction_tasks)  # Bot doesn't have permission to add reactions

    @classmethod
    @override
    async def from_id(cls, bot: "squid.bot.RedstoneSquid", vote_session_id: int) -> "DeleteLogVoteSession | None":
        snapshot = await bot.services.votes.get_session_by_id(vote_session_id)
        if snapshot is None or snapshot.kind != cls.kind:
            return None
        return await cls._from_snapshot(bot, snapshot)

    @classmethod
    async def _from_snapshot(
        cls, bot: "squid.bot.RedstoneSquid", snapshot: VoteSessionSnapshot
    ) -> "DeleteLogVoteSession | None":
        """Restore a Discord view from an application snapshot."""
        target = snapshot.target
        if target.channel_id is None or target.message_id is None:
            return None
        target_message = await bot.get_or_fetch_message(target.channel_id, target.message_id)
        if target_message is None:
            return None

        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            bot,
            snapshot.message_ids,
            snapshot.author_id,
            target_message,
            snapshot.pass_threshold,
            snapshot.fail_threshold,
            snapshot.options,
        )
        self.id = snapshot.id
        self._message_channels = {message.id: message.channel_id for message in snapshot.messages}
        self.apply_persisted_state(snapshot)
        return self

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial message to the channel."""
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=(
                dedent(f"""
                React with {self.primary_emoji(VoteChoice.APPROVE)} to upvote or {self.primary_emoji(VoteChoice.DENY)} to downvote.\n\n
                **Log Content:**\n{self.target_message.content}\n\n
                **Upvotes:** {self.upvotes}
                **Downvotes:** {self.downvotes}
                **Net Votes:** {self.net_votes}""")
            ),
        )
        return await channel.send(embed=embed)

    @override
    async def update_messages(self) -> None:
        """Updates the message with the current vote count."""
        match self.result:
            case "pending":
                title = "Vote to Delete Log"
                action = (
                    f"React with {self.primary_emoji(VoteChoice.APPROVE)} to upvote or "
                    f"{self.primary_emoji(VoteChoice.DENY)} to downvote.\n\n"
                )
            case "approved":
                title = "Vote to Delete Log: Passed"
                action = ""
            case "denied":
                title = "Vote to Delete Log: Failed"
                action = ""
            case _:
                title = "Vote to Delete Log: Closed"
                action = ""

        embed = discord.Embed(
            title=title,
            description=(
                dedent(f"""
                {action}**Log Content:**\n{self.target_message.content}\n\n
                **Upvotes:** {self.upvotes}
                **Downvotes:** {self.downvotes}
                **Net Votes:** {self.net_votes}""")
            ),
        )
        await asyncio.gather(*[message.edit(embed=embed) for message in await self.fetch_messages()])

    @classmethod
    async def get_open_vote_sessions(
        cls: "type[DeleteLogVoteSession]", bot: "squid.bot.RedstoneSquid"
    ) -> "list[DeleteLogVoteSession]":
        """Get all open vote sessions from the database."""
        sessions = await asyncio.gather(
            *(cls._from_snapshot(bot, snapshot) for snapshot in await bot.services.votes.list_open(cls.kind))
        )
        return [session for session in sessions if session is not None]
