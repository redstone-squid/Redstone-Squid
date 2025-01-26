"""A vote session that represents a change to something."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import inspect
from abc import abstractmethod, ABC
import asyncio
from asyncio import Task
from textwrap import dedent
from types import MethodType
from typing import Any, Self, TYPE_CHECKING, cast, ClassVar, final, Literal, override

import discord
from postgrest.base_request_builder import APIResponse, SingleAPIResponse

from squid.db import DatabaseManager
from squid.db.builds import Build
from squid.db.schema import VoteKind, MessageRecord, Status, VoteSessionRecord

if TYPE_CHECKING:
    from squid.bot.main import RedstoneSquid


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]
# TODO: Unhardcode these emojis


async def track_vote_session(
    messages: Iterable[discord.Message],
    author_id: int,
    kind: VoteKind,
    pass_threshold: int,
    fail_threshold: int,
    *,
    build_id: int | None = None,
) -> int:
    """Track a vote session in the database.

    Args:
        messages: The messages belonging to the vote session.
        author_id: The discord id of the author of the vote session.
        kind: The type of vote session.
        pass_threshold: The number of votes required to pass the vote.
        fail_threshold: The number of votes required to fail the vote.
        build_id: The id of the build to vote on. None if the vote is not about a build.

    Returns:
        The id of the vote session.
    """
    db = DatabaseManager()
    response: APIResponse[VoteSessionRecord] = (
        await db.table("vote_sessions")
        .insert(
            {
                "status": "open",
                "author_id": author_id,
                "kind": kind,
                "pass_threshold": pass_threshold,
                "fail_threshold": fail_threshold,
            }
        )
        .execute()
    )
    session_id = response.data[0]["id"]
    coros = [
        db.message.track_message(message, "vote", build_id=build_id, vote_session_id=session_id) for message in messages
    ]
    await asyncio.gather(*coros)
    return session_id


async def close_vote_session(vote_session_id: int) -> None:
    """Close a vote session in the database.

    Args:
        vote_session_id: The id of the vote session.
    """
    db = DatabaseManager()
    await db.table("vote_sessions").update({"status": "closed"}).eq("id", vote_session_id).execute()


async def upsert_vote(vote_session_id: int, user_id: int, weight: float | None) -> None:
    """Upsert a vote in the database.

    Args:
        vote_session_id: The id of the vote session.
        user_id: The id of the user voting.
        weight: The weight of the vote. None to remove the vote.
    """
    db = DatabaseManager()
    await db.table("votes").upsert({"vote_session_id": vote_session_id, "user_id": user_id, "weight": weight}).execute()


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

    kind: ClassVar[VoteKind]

    def __init__(
        self,
        bot: RedstoneSquid,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
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
        """Perform async initialization. Called by create()."""
        self.id = await track_vote_session(
            self._messages, self.author_id, self.kind, self.pass_threshold, self.fail_threshold
        )
        await self.update_messages()

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
    async def from_id(cls: type[Self], bot: RedstoneSquid, vote_session_id: int) -> Self | None:
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
            *(self.bot.get_or_fetch_message(record["channel_id"], record["message_id"]) for record in messages_record.data if record["message_id"] not in cached_ids)
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
            return

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
            return

        if weight is None:
            self._votes.pop(user_id, None)
        else:
            self._votes[user_id] = weight

        if not self.fail_threshold < self.net_votes < self.pass_threshold:
            await self.close()

        if self.id is not None:
            await upsert_vote(self.id, user_id, weight)


@final
class BuildVoteSession(AbstractVoteSession):
    """A vote session for a confirming or denying a build."""

    kind = "build"

    def __init__(
        self,
        bot: RedstoneSquid,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
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
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold)
        self.build = build
        self.type = type

    @classmethod
    @override
    async def create(
        cls,
        bot: RedstoneSquid,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        build: Build,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> "BuildVoteSession":
        self = await super().create(bot, messages, author_id, build, type, pass_threshold, fail_threshold)
        assert isinstance(self, BuildVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        self.id = await track_vote_session(
            await self.fetch_messages(),
            self.author_id,
            self.kind,
            self.pass_threshold,
            self.fail_threshold,
            build_id=self.build.id,
        )
        assert self.build.id is not None
        if self.type == "add":
            changes = [("submission_status", Status.PENDING, Status.CONFIRMED)]
        else:
            original = await Build.from_id(self.build.id)
            assert original is not None
            changes = original.diff(self.build)

        await (
            DatabaseManager()
            .table("build_vote_sessions")
            .insert(
                {
                    "vote_session_id": self.id,
                    "build_id": self.build.id,
                    "changes": changes,
                }
            )
            .execute()
        )

        await self.update_messages()

        reaction_tasks = [message.add_reaction(APPROVE_EMOJIS[0]) for message in self._messages]
        reaction_tasks.extend([message.add_reaction(DENY_EMOJIS[0]) for message in self._messages])
        try:
            await asyncio.gather(*reaction_tasks)
        except discord.Forbidden:
            pass  # Bot doesn't have permission to add reactions

    @classmethod
    @override
    async def from_id(cls, bot: RedstoneSquid, vote_session_id: int) -> BuildVoteSession | None:
        vote_session_response: SingleAPIResponse[dict[str, Any]] | None = (
            await bot.db.table("vote_sessions")
            .select("*, messages(*), votes(*), build_vote_sessions(*)")
            .eq("id", vote_session_id)
            .eq("kind", cls.kind)
            .maybe_single()
            .execute()
        )
        if vote_session_response is None:
            return None

        vote_session_record = vote_session_response.data
        return await cls._from_record(bot, vote_session_record)

    @classmethod
    async def _from_record(cls, bot: RedstoneSquid, record: dict[str, Any]) -> BuildVoteSession:
        """Create a vote session from a database record."""
        if record["build_vote_sessions"] is None:
            raise ValueError(f"Found a build vote session with no associated build id. session_id={record["id"]}")
        build_id: int = record["build_vote_sessions"]["build_id"]
        build = await Build.from_id(build_id)

        assert build is not None
        session = cls.__new__(cls)
        session._allow_init = True
        session.__init__(
            bot=bot,
            messages=[msg["message_id"] for msg in record["messages"]],
            author_id=record["author_id"],
            build=build,
            type="add",
            pass_threshold=record["pass_threshold"],
            fail_threshold=record["fail_threshold"],
        )
        # We can skip _async_init because we already have the id and everything has been tracked before
        session.id = record["id"]
        session._votes = {vote["user_id"]: vote["weight"] for vote in record["votes"]}

        return session

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        message = await channel.send(content=self.build.original_link, embed=await self.build.generate_embed())
        await self.bot.db.message.track_message(
            message, purpose="vote", build_id=self.build.id, vote_session_id=self.id
        )
        self._messages.add(message)
        return message

    @override
    async def update_messages(self):
        embed = await self.build.generate_embed()
        embed.add_field(name="", value="", inline=False)  # Add a blank field to separate the vote count
        embed.add_field(name="Accept", value=f"{self.upvotes}/{self.pass_threshold}", inline=True)
        embed.add_field(name="Deny", value=f"{self.downvotes}/{-self.fail_threshold}", inline=True)
        await asyncio.gather(
            *[message.edit(content=self.build.original_link, embed=embed) for message in await self.fetch_messages()]
        )

    @override
    async def close(self) -> None:
        if self.is_closed:
            return

        self.is_closed = True
        if self.net_votes < self.pass_threshold:
            await self.build.deny()
        else:
            await self.build.confirm()
        # TODO: decide whether to delete the messages or not

        await self.update_messages()

        if self.id is not None:
            await close_vote_session(self.id)

    @classmethod
    async def get_open_vote_sessions(cls: type[BuildVoteSession], bot: RedstoneSquid) -> list[BuildVoteSession]:
        """Get all open vote sessions from the database."""
        records: list[dict[str, Any]] = (
            await bot.db.table("vote_sessions")
            .select("*, messages(*), votes(*), build_vote_sessions(*)")
            .eq("status", "open")
            .eq("kind", cls.kind)
            .execute()
        ).data

        return await asyncio.gather(*[cls._from_record(bot, record) for record in records])


@final
class DeleteLogVoteSession(AbstractVoteSession):
    """A vote session for deleting a message from the log."""

    kind = "delete_log"

    def __init__(
        self,
        bot: RedstoneSquid,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
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
        super().__init__(bot, messages, author_id, pass_threshold, fail_threshold)
        self.target_message = target_message

    @classmethod
    @override
    async def create(
        cls,
        bot: RedstoneSquid,
        messages: Iterable[discord.Message] | Iterable[int],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> "DeleteLogVoteSession":
        self = await super().create(bot, messages, author_id, target_message, pass_threshold, fail_threshold)
        assert isinstance(self, DeleteLogVoteSession)
        return self

    @override
    async def _async_init(self) -> None:
        """Track the vote session in the database."""
        self.id = await track_vote_session(
            await self.fetch_messages(), self.author_id, self.kind, self.pass_threshold, self.fail_threshold
        )
        await (
            DatabaseManager()
            .table("delete_log_vote_sessions")
            .insert(
                {
                    "vote_session_id": self.id,
                    "target_message_id": self.target_message.id,
                    "target_channel_id": self.target_message.channel.id,
                    "target_server_id": self.target_message.guild.id,  # type: ignore
                }
            )
            .execute()
        )
        await self.update_messages()
        reaction_tasks = [message.add_reaction(APPROVE_EMOJIS[0]) for message in self._messages]
        reaction_tasks.extend(message.add_reaction(DENY_EMOJIS[0]) for message in self._messages)
        try:
            await asyncio.gather(*reaction_tasks)
        except discord.Forbidden:
            pass  # Bot doesn't have permission to add reactions

    @classmethod
    @override
    async def from_id(cls, bot: RedstoneSquid, vote_session_id: int) -> DeleteLogVoteSession | None:
        vote_session_response: SingleAPIResponse[dict[str, Any]] | None = (
            await bot.db.table("vote_sessions")
            .select("*, messages(*), votes(*), delete_log_vote_sessions(*)")
            .eq("id", vote_session_id)
            .eq("kind", cls.kind)
            .maybe_single()
            .execute()
        )
        if vote_session_response is None:
            return None

        vote_session_record = vote_session_response.data
        target_message = await bot.get_or_fetch_message(
            vote_session_record["delete_log_vote_sessions"]["target_channel_id"],
            vote_session_record["delete_log_vote_sessions"]["target_message_id"],
        )
        if target_message is None:
            return None

        return await cls._from_record(bot, vote_session_record)

    @classmethod
    async def _from_record(cls, bot: RedstoneSquid, record: Mapping[str, Any]) -> DeleteLogVoteSession | None:
        """Create a DeleteLogVoteSession from a database record."""
        session_data = record["delete_log_vote_sessions"]
        target_message = await bot.get_or_fetch_message(session_data["target_channel_id"], session_data["target_message_id"])
        if target_message is None:
            return None

        self = cls.__new__(cls)
        self._allow_init = True
        self.__init__(
            bot,
            [msg["message_id"] for msg in record["messages"]],
            record["author_id"],
            target_message,
            record["pass_threshold"],
            record["fail_threshold"],
        )
        self.id = record[
            "id"
        ]  # We can skip _async_init because we already have the id and everything has been tracked before
        self._votes = {vote["user_id"]: vote["weight"] for vote in record["votes"]}
        return self

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial message to the channel."""
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=(
                dedent(f"""
                React with {APPROVE_EMOJIS[0]} to upvote or {DENY_EMOJIS[0]} to downvote.\n\n
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
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=(
                dedent(f"""
                React with {APPROVE_EMOJIS[0]} to upvote or {DENY_EMOJIS[0]} to downvote.\n\n
                **Log Content:**\n{self.target_message.content}\n\n
                **Upvotes:** {self.upvotes}
                **Downvotes:** {self.downvotes}
                **Net Votes:** {self.net_votes}""")
            ),
        )
        await asyncio.gather(*[message.edit(embed=embed) for message in await self.fetch_messages()])

    @override
    async def close(self) -> None:
        if self.is_closed:
            return

        self.is_closed = True
        if self.net_votes <= self.pass_threshold:
            await asyncio.gather(*[message.channel.send("Vote failed") for message in await self.fetch_messages()])
        else:
            await self.target_message.delete()

        if self.id is not None:
            await close_vote_session(self.id)

    @classmethod
    async def get_open_vote_sessions(cls: type[DeleteLogVoteSession], bot: RedstoneSquid) -> list[DeleteLogVoteSession]:
        """Get all open vote sessions from the database."""
        records: list[VoteSessionRecord] = (
            await bot.db.table("vote_sessions")
            .select("*, messages(*), votes(*), delete_log_vote_sessions(*)")
            .eq("status", "open")
            .eq("kind", cls.kind)
            .execute()
        ).data

        sessions = await asyncio.gather(*[cls._from_record(bot, record) for record in records])
        return [session for session in sessions if session is not None]
