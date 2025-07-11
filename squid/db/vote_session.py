"""A vote session that represents a change to something."""

import asyncio
import contextlib
import inspect
import logging
from abc import ABC, abstractmethod
from asyncio import Task
from collections.abc import Iterable
from typing import Any, ClassVar, Literal, Self, TypeVar, final, override

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from squid.db import DatabaseManager
from squid.db.builds import Build
from squid.db.schema import BuildVoteSession as SQLBuildVoteSession, VoteSessionResultLiteral
from squid.db.schema import DeleteLogVoteSession as SQLDeleteLogVoteSession
from squid.db.schema import Message, Vote, VoteKindLiteral, VoteSession, VoteSessionEmoji

logger = logging.getLogger(__name__)


_SelfT = TypeVar(
    "_SelfT"
)  # There is no parallel construct to typing.Self for class methods, so we have to make a workaround


async def upsert_vote(vote_session_id: int, user_id: int, weight: float | None, emoji: str | None = None) -> None:
    """Upsert a vote in the database.

    Args:
        vote_session_id: The id of the vote session.
        user_id: The id of the user voting.
        weight: The weight of the vote. None to remove the vote.
        emoji: The emoji used for the vote.
    """
    db = DatabaseManager()
    async with db.async_session() as session:
        stmt = (
            pg_insert(Vote)
            .values(vote_session_id=vote_session_id, user_id=user_id, weight=weight, emoji=emoji)
            .on_conflict_do_update(
                index_elements=[Vote.vote_session_id, Vote.user_id], set_=dict(weight=weight, emoji=emoji)
            )
        )
        await session.execute(stmt)
        await session.commit()


async def get_vote_session_by_id(
    vote_session_id: int, *, status: Literal["open", "closed"] | None = None, kind: VoteKindLiteral | None = None
) -> "AbstractVoteSession | None":
    """Gets a vote session from the database.

    Args:
        vote_session_id: The id of the vote session.
        status: The status of the vote session. If None, it will get any status.
        kind: The kind of the vote session. If None, it will get any kind.

    Returns:
        The vote session if it exists, otherwise None.

    Raises:
        NotImplementedError: If the vote session kind is unknown.
    """
    stmt = select(VoteSession.id, VoteSession.kind).where(VoteSession.id == vote_session_id)
    if status is not None:
        stmt = stmt.where(VoteSession.status == status)
    if kind is not None:
        stmt = stmt.where(VoteSession.kind == kind)

    async with DatabaseManager().async_session() as session:
        result = await session.execute(stmt)
        result_tup = result.one_or_none()
        if result_tup is None:
            return None
        vote_session_id, kind = result_tup

    if kind == "build":
        return await BuildVoteSession.from_id(vote_session_id)
    if kind == "delete_log":
        return await DeleteLogVoteSession.from_id(vote_session_id)
    logger.error("Unknown vote session kind: %s", kind)
    msg = f"Unknown vote session kind: {kind}"
    raise NotImplementedError(msg)


async def get_vote_session_from_message_id(
    message_id: int, *, status: Literal["open", "closed"] | None = None
) -> "AbstractVoteSession | None":
    """Gets a vote session from the database.

    Args:
        message_id: The message ID of the vote session.
        status: The status of the vote session. If None, it will get any status.

    Returns:
        The vote session if it exists, otherwise None.

    Raises:
        NotImplementedError: If the message corresponds to an unknown vote session kind.
    """
    stmt = (
        select(Message.vote_session_id)
        .where(Message.id == message_id, Message.purpose == "vote")
    )

    async with DatabaseManager().async_session() as session:
        result = await session.execute(stmt)
        vote_session_id = result.scalar_one_or_none()

    if vote_session_id is None:
        return None
    return await get_vote_session_by_id(vote_session_id, status=status)

async def get_emoji_multiplier(vote_session_id: int, emoji: str) -> float | None:
    """Gets the multiplier for an emoji in a vote session.

    Args:
        vote_session_id: The id of the vote session.
        emoji: The emoji to get the multiplier for.

    Returns:
        The multiplier (float) if the emoji is associated with the vote session, otherwise None.
    """
    stmt = select(VoteSessionEmoji.default_multiplier).where(
        VoteSessionEmoji.vote_session_id == vote_session_id, VoteSessionEmoji.emoji == emoji
    )
    async with DatabaseManager().async_session() as session:
        result = await session.execute(stmt)
        multiplier = result.scalar_one_or_none()
    return multiplier


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

    kind: ClassVar[VoteKindLiteral]

    def __init__(
        self,
        message_ids: Iterable[int],
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        approve_emojis: list[str],
        deny_emojis: list[str],
    ):
        """
        Initialize the vote session, this should be called by subclasses only. Use create() instead.

        If you use this constructor directly, you must call _async_init() afterwards, or else the vote session will not be tracked.

        Args:
            message_ids: The messages (or their ids) belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
            approve_emojis: The emojis to use for approving the vote.
            deny_emojis: The emojis to use for denying the vote.
        """
        self._allow_init: bool
        """A flag to allow direct initialization."""
        if getattr(self, "_allow_init", False) is not True:
            msg = "Do not use __init__ directly, use create() instead."
            raise ValueError(msg)

        super().__init__()
        self.id: int | None = None
        """The id of the vote session in the database."""
        self.is_closed = False
        self.message_ids = message_ids
        self.author_id = author_id
        self.pass_threshold = pass_threshold
        self.fail_threshold = fail_threshold
        self.approve_emojis = approve_emojis
        self.deny_emojis = deny_emojis
        self._votes: dict[int, float] = {}  # Dict of user_id: weight
        self._tasks: set[Task[Any]] = set()

    @classmethod
    @abstractmethod
    async def from_id(cls: type[Self], vote_session_id: int) -> Self | None:
        """Fetch a vote session from its id.

        Returns:
            The vote session if it exists, otherwise None.
        """
        raise NotImplementedError

    @final
    @property
    def upvotes(self) -> float:
        """Total score of upvotes"""
        return sum(vote for vote in self._votes.values() if vote > 0)

    @final
    @property
    def downvotes(self) -> float:
        """Total score of downvotes"""
        return -sum(vote for vote in self._votes.values() if vote < 0)

    @final
    @property
    def net_votes(self) -> float:
        """Net vote score"""
        return sum(self._votes.values())

    @final
    @property
    def status(self) -> Literal["open", "closed"]:
        """The current status, "open" means the vote session is still accepting votes, "closed" means it has been finalized."""
        if self.is_closed:
            return "closed"
        return "open"

    @final
    @property
    def result(self) -> VoteSessionResultLiteral:
        """The result of the vote session. If the session is closed, it will return "approved" if the vote passed, "denied" if it failed, or "cancelled" if it was closed without a decision. If the session is still open, it will return "pending"."""
        if self.is_closed:
            if self.net_votes >= self.pass_threshold:
                return "approved"
            elif self.net_votes <= self.fail_threshold:
                return "denied"
            else:
                return "cancelled"
        return "pending"

    @final
    def __getitem__(self, user_id: int) -> float | None:
        return self._votes.get(user_id)

    @final
    def __contains__(self, user_id: int) -> bool:
        """Check if a user has voted in this session."""
        return user_id in self._votes

    @final
    async def set_vote(self, user_id: int, weight: float | None, emoji: str | None = None) -> None:
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
            await upsert_vote(self.id, user_id, weight, emoji)

    @final
    async def get_emoji_multiplier(self, emoji: str) -> float | None:
        """Get the multiplier for an emoji in this vote session."""
        if self.id is not None:
            return await get_emoji_multiplier(self.id, emoji)
        raise NotImplementedError("The data in AbstractVoteSession is not enough to get emoji multipliers.")

    @abstractmethod
    async def close(self) -> None:
        """Close the vote session"""
        self.is_closed = True
        # Wait for any pending vote operations
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=False)
        assert self.id is not None
        await self._close_vote_session()

    async def _close_vote_session(self) -> None:
        """Close a vote session in the database."""
        db = DatabaseManager()
        async with db.async_session() as session:
            stmt = update(VoteSession).where(VoteSession.id == self.id).values(status="closed", result=self.result)
            await session.execute(stmt)
            await session.commit()

    @classmethod
    @abstractmethod
    async def get_open_sessions(cls: type[_SelfT]) -> "list[_SelfT]":
        """Get all open vote sessions from the database."""
        raise NotImplementedError


@final
class BuildVoteSession(AbstractVoteSession):
    """A vote session for a confirming or denying a build."""

    kind: ClassVar[Literal["build"]] = "build"  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        message_ids: Iterable[int],
        author_id: int,
        build: Build,
        diff: list[tuple[str, Any, Any]],
        pass_threshold: int,
        fail_threshold: int,
        approve_emojis: list[str],
        deny_emojis: list[str],
    ):
        """
        Initialize the vote session.

        Args:
            message_ids: The message ids belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            build: The build which the vote session is for. If type is "update", this is the updated build.
            diff: The differences between the original and updated build, if applicable.
                If you are trying to add a new build, it is modelled as the build already existing with a pending status. So you should pass a diff with the status changed from PENDING to CONFIRMED.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
            approve_emojis: The emojis to use for approving the vote.
            deny_emojis: The emojis to use for denying the vote.
        """
        super().__init__(message_ids, author_id, pass_threshold, fail_threshold, approve_emojis, deny_emojis)
        self.build = build
        self.diff = diff

    @classmethod
    @override
    async def from_id(cls, vote_session_id: int) -> "BuildVoteSession | None":
        async with DatabaseManager().async_session() as session:
            stmt = insert(SQLBuildVoteSession).values(
                vote_session_id=self.id,
                build_id=self.build.id,
                changes=changes,
            )
            await session.execute(stmt)
            await session.commit()

        await self.update_messages()

        reaction_tasks = [message.add_reaction(APPROVE_EMOJIS[0]) for message in self._messages]
        reaction_tasks.extend([message.add_reaction(DENY_EMOJIS[0]) for message in self._messages])
        with contextlib.suppress(discord.Forbidden):
            await asyncio.gather(*reaction_tasks)  # Bot doesn't have permission to add reactions

    @classmethod
    @override
    async def from_id(cls, bot: "squid.bot.RedstoneSquid", vote_session_id: int) -> "BuildVoteSession | None":
        async with bot.db.async_session() as session:
            stmt = select(SQLBuildVoteSession).where(SQLBuildVoteSession.id == vote_session_id)
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record is None:
                return None
            return await cls._from_domain(record)

    @classmethod
    async def _from_domain(cls, record: SQLBuildVoteSession) -> "BuildVoteSession":
        """Create a vote session from a database record."""
        if record.build_id is None:  # pyright: ignore[reportUnnecessaryComparison]
            msg = f"Found a build vote session with no associated build id. session_id={record.id}"
            raise ValueError(msg)

        build = DatabaseManager().build.from_sql_build(record.build)
        assert build is not None
        self = BuildVoteSession(
            message_ids=[msg.id for msg in record.messages],
            author_id=record.author_id,
            build=build,
            diff=record.changes,
            pass_threshold=record.pass_threshold,
            fail_threshold=record.fail_threshold,
            approve_emojis=[j.emoji for j in record.vote_session_emojis if j.default_multiplier >= 0],
            deny_emojis=[j.emoji for j in record.vote_session_emojis if j.default_multiplier < 0],
        )
        # We can skip _async_init because we already have the id and everything has been tracked before
        self.id = record.id
        self._votes = {vote.user_id: vote.weight for vote in record.votes}
        self.is_closed = record.status == "closed"
        return self

    @override
    async def close(self) -> None:
        if self.is_closed:
            return

        self.is_closed = True
        if self.net_votes < self.pass_threshold:
            await self.build.deny()
        else:
            await self.build.confirm()

        if self.id is not None:
            await self._close_vote_session()

    @classmethod
    @override
    async def get_open_sessions(cls: type["BuildVoteSession"]) -> "list[BuildVoteSession]":
        """Get all open vote sessions from the database."""
        stmt = select(SQLBuildVoteSession).where(SQLBuildVoteSession.status == "open")
        async with DatabaseManager().async_session() as session:
            result = await session.execute(stmt)
            records = result.scalars().all()

        vote_sessions = await asyncio.gather(*(cls._from_domain(record) for record in records))
        assert all(vs.status == "open" for vs in vote_sessions)
        return vote_sessions


@final
class DeleteLogVoteSession(AbstractVoteSession):
    """A vote session for deleting a message from the log."""

    kind: ClassVar[Literal["delete_log"]] = "delete_log"  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(
        self,
        message_ids: Iterable[int],
        author_id: int,
        target_message_id: int,
        target_channel_id: int,
        target_server_id: int,
        pass_threshold: int,
        fail_threshold: int,
        approve_emojis: list[str],
        deny_emojis: list[str],
    ):
        """
        Initialize the delete log vote session.

        Args:
            message_ids: The messages ids belonging to the vote session.
            author_id: The discord id of the author of the vote session.
            target_message_id: The message id to delete if the vote passes.
            target_channel_id: The channel id of the message to delete.
            target_server_id: The server id of the message to delete.
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
            approve_emojis: The emojis to use for approving the vote.
            deny_emojis: The emojis to use for denying the vote.
        """
        super().__init__(message_ids, author_id, pass_threshold, fail_threshold, approve_emojis, deny_emojis)
        self.target_message_id = target_message_id
        self.target_channel_id = target_channel_id
        self.target_server_id = target_server_id

    @classmethod
    @override
    async def from_id(cls, vote_session_id: int) -> "DeleteLogVoteSession | None":
        stmt = select(SQLDeleteLogVoteSession).where(SQLDeleteLogVoteSession.id == vote_session_id)
        async with DatabaseManager().async_session() as session:
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()

        if record is None:
            return None
        return await cls._from_domain(record)

    @staticmethod
    async def _from_domain(record: SQLDeleteLogVoteSession) -> "DeleteLogVoteSession":
        """Create a DeleteLogVoteSession from a database record."""
        self = DeleteLogVoteSession(
            [msg.id for msg in record.messages],
            record.author_id,
            record.target_message_id,
            record.target_channel_id,
            record.target_server_id,
            record.pass_threshold,
            record.fail_threshold,
            [j.emoji for j in record.vote_session_emojis if j.default_multiplier >= 0],
            [j.emoji for j in record.vote_session_emojis if j.default_multiplier < 0],
        )
        self.id = record.vote_session_id
        self._votes = {vote.user_id: vote.weight for vote in record.votes}
        self.is_closed = record.status == "closed"
        return self

    @override
    async def close(self) -> None:
        if self.is_closed:
            return

        self.is_closed = True
        if self.id is not None:
            await self._close_vote_session()

    @classmethod
    @override
    async def get_open_sessions(cls: "type[DeleteLogVoteSession]") -> "list[DeleteLogVoteSession]":
        """Get all open vote sessions from the database."""
        stmt = select(SQLDeleteLogVoteSession).where(
            SQLDeleteLogVoteSession.status == "open", VoteSession.kind == cls.kind
        )
        async with DatabaseManager().async_session() as session:
            result = await session.execute(stmt)
            records = result.scalars().all()

        vote_sessions = await asyncio.gather(*(cls._from_domain(record) for record in records))
        assert all(vs.status == "open" for vs in vote_sessions)
        return vote_sessions
