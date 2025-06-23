"""Handles vote session data and operations."""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, TypedDict

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from squid.db.schema import (
    BuildVoteSession as SQLBuildVoteSession,
    DeleteLogVoteSession as SQLDeleteLogVoteSession,
    Message,
    Vote,
    VoteSession,
    VoteSessionRecord,
)

if TYPE_CHECKING:
    import squid.bot


class VoteRecord(TypedDict):
    """A record of a vote in the database."""

    vote_session_id: int
    user_id: int
    weight: float


class VoteSessionManager:
    """A class for managing vote session data and operations."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self.session = session

    async def create_vote_session(
        self,
        author_id: int,
        kind: str,
        pass_threshold: int,
        fail_threshold: int,
        *,
        build_id: int | None = None,
    ) -> int:
        """Create a new vote session in the database.

        Args:
            author_id: The Discord ID of the vote session author.
            kind: The type of vote session (e.g., "build", "delete_log").
            pass_threshold: The number of votes required to pass the vote.
            fail_threshold: The number of votes required to fail the vote.
            build_id: The ID of the build to vote on. None if the vote is not about a build.

        Returns:
            The ID of the created vote session.
        """
        async with self.session() as session:
            stmt = (
                insert(VoteSession)
                .values(
                    status="open",
                    author_id=author_id,
                    kind=kind,
                    pass_threshold=pass_threshold,
                    fail_threshold=fail_threshold,
                )
                .returning(VoteSession)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.scalar_one().id

    async def create_build_vote_session(
        self,
        vote_session_id: int,
        build_id: int,
        changes: list[tuple[str, object, object]],
    ) -> None:
        """Create a build vote session record.

        Args:
            vote_session_id: The ID of the vote session.
            build_id: The ID of the build being voted on.
            changes: The changes that will be applied if the vote passes.
        """
        async with self.session() as session:
            stmt = insert(SQLBuildVoteSession).values(
                vote_session_id=vote_session_id,
                build_id=build_id,
                changes=changes,
            )
            await session.execute(stmt)
            await session.commit()

    async def create_delete_log_vote_session(
        self,
        vote_session_id: int,
        target_message_id: int,
        target_channel_id: int,
        target_server_id: int,
    ) -> None:
        """Create a delete log vote session record.

        Args:
            vote_session_id: The ID of the vote session.
            target_message_id: The ID of the message to be deleted.
            target_channel_id: The ID of the channel containing the message.
            target_server_id: The ID of the server containing the message.
        """
        async with self.session() as session:
            stmt = insert(SQLDeleteLogVoteSession).values(
                vote_session_id=vote_session_id,
                target_message_id=target_message_id,
                target_channel_id=target_channel_id,
                target_server_id=target_server_id,
            )
            await session.execute(stmt)
            await session.commit()

    async def get_vote_session_by_id(self, vote_session_id: int) -> VoteSessionRecord | None:
        """Get a vote session by its ID.

        Args:
            vote_session_id: The ID of the vote session.

        Returns:
            The vote session record, or None if not found.
        """
        async with self.session() as session:
            stmt = select(VoteSession).where(VoteSession.id == vote_session_id)
            result = await session.execute(stmt)
            vote_session = result.scalar_one_or_none()
            
            if vote_session is None:
                return None
                
            return VoteSessionRecord(
                id=vote_session.id,
                status=vote_session.status,  # type: ignore
                author_id=vote_session.author_id,
                kind=vote_session.kind,
                pass_threshold=vote_session.pass_threshold,
                fail_threshold=vote_session.fail_threshold,
                created_at=vote_session.created_at,
            )

    async def get_vote_session_by_message_id(
        self, message_id: int, *, status: Literal["open", "closed"] | None = None
    ) -> VoteSessionRecord | None:
        """Get a vote session by a message ID.

        Args:
            message_id: The ID of the message.
            status: The status of the vote session. If None, it will get any status.

        Returns:
            The vote session record, or None if not found.
        """
        async with self.session() as session:
            stmt = (
                select(VoteSession)
                .options(selectinload(VoteSession.messages))
                .join(Message, VoteSession.messages)
                .where(Message.id == message_id, Message.purpose == "vote")
            )
            
            if status is not None:
                stmt = stmt.where(VoteSession.status == status)
                
            result = await session.execute(stmt)
            vote_session = result.scalar_one_or_none()
            
            if vote_session is None:
                return None
                
            return VoteSessionRecord(
                id=vote_session.id,
                status=vote_session.status,  # type: ignore
                author_id=vote_session.author_id,
                kind=vote_session.kind,
                pass_threshold=vote_session.pass_threshold,
                fail_threshold=vote_session.fail_threshold,
                created_at=vote_session.created_at,
            )

    async def get_open_vote_sessions(self, kind: str | None = None) -> list[VoteSessionRecord]:
        """Get all open vote sessions.

        Args:
            kind: The kind of vote session to filter by. If None, returns all kinds.

        Returns:
            A list of open vote session records.
        """
        async with self.session() as session:
            stmt = select(VoteSession).where(VoteSession.status == "open")
            
            if kind is not None:
                stmt = stmt.where(VoteSession.kind == kind)
                
            result = await session.execute(stmt)
            vote_sessions = result.scalars().all()
            
            return [
                VoteSessionRecord(
                    id=vs.id,
                    status=vs.status,  # type: ignore
                    author_id=vs.author_id,
                    kind=vs.kind,
                    pass_threshold=vs.pass_threshold,
                    fail_threshold=vs.fail_threshold,
                    created_at=vs.created_at,
                )
                for vs in vote_sessions
            ]

    async def get_build_vote_session(self, vote_session_id: int) -> SQLBuildVoteSession | None:
        """Get a build vote session by vote session ID.

        Args:
            vote_session_id: The ID of the vote session.

        Returns:
            The build vote session record, or None if not found.
        """
        async with self.session() as session:
            stmt = (
                select(SQLBuildVoteSession)
                .options(selectinload(SQLBuildVoteSession.build))
                .options(selectinload(SQLBuildVoteSession.messages))
                .options(selectinload(SQLBuildVoteSession.votes))
                .where(SQLBuildVoteSession.vote_session_id == vote_session_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_delete_log_vote_session(self, vote_session_id: int) -> SQLDeleteLogVoteSession | None:
        """Get a delete log vote session by vote session ID.

        Args:
            vote_session_id: The ID of the vote session.

        Returns:
            The delete log vote session record, or None if not found.
        """
        async with self.session() as session:
            stmt = (
                select(SQLDeleteLogVoteSession)
                .options(selectinload(SQLDeleteLogVoteSession.messages))
                .options(selectinload(SQLDeleteLogVoteSession.votes))
                .where(SQLDeleteLogVoteSession.vote_session_id == vote_session_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def close_vote_session(self, vote_session_id: int) -> None:
        """Close a vote session.

        Args:
            vote_session_id: The ID of the vote session to close.
        """
        async with self.session() as session:
            stmt = update(VoteSession).where(VoteSession.id == vote_session_id).values(status="closed")
            await session.execute(stmt)
            await session.commit()

    async def upsert_vote(self, vote_session_id: int, user_id: int, weight: float | None) -> None:
        """Upsert a vote in the database.

        Args:
            vote_session_id: The ID of the vote session.
            user_id: The ID of the user voting.
            weight: The weight of the vote. None to remove the vote.
        """
        async with self.session() as session:
            if weight is None:
                # Remove the vote
                stmt = delete(Vote).where(
                    Vote.vote_session_id == vote_session_id,
                    Vote.user_id == user_id,
                )
            else:
                # Insert or update the vote
                stmt = (
                    pg_insert(Vote)
                    .values(
                        vote_session_id=vote_session_id,
                        user_id=user_id,
                        weight=weight,
                    )
                    .on_conflict_do_update(
                        index_elements=[Vote.vote_session_id, Vote.user_id],
                        set_=dict(weight=weight)
                    )
                )
            
            await session.execute(stmt)
            await session.commit()

    async def get_votes(self, vote_session_id: int) -> list[VoteRecord]:
        """Get all votes for a vote session.

        Args:
            vote_session_id: The ID of the vote session.

        Returns:
            A list of vote records.
        """
        async with self.session() as session:
            stmt = select(Vote).where(Vote.vote_session_id == vote_session_id)
            result = await session.execute(stmt)
            votes = result.scalars().all()
            
            return [
                VoteRecord(
                    vote_session_id=vote.vote_session_id,
                    user_id=vote.user_id,
                    weight=vote.weight,
                )
                for vote in votes
            ]

    async def get_vote(self, vote_session_id: int, user_id: int) -> float | None:
        """Get a specific vote.

        Args:
            vote_session_id: The ID of the vote session.
            user_id: The ID of the user.

        Returns:
            The weight of the vote, or None if no vote exists.
        """
        async with self.session() as session:
            stmt = select(Vote).where(
                Vote.vote_session_id == vote_session_id,
                Vote.user_id == user_id,
            )
            result = await session.execute(stmt)
            vote = result.scalar_one_or_none()
            
            return vote.weight if vote is not None else None

    async def get_vote_summary(self, vote_session_id: int) -> tuple[float, float, float]:
        """Get a summary of votes for a vote session.

        Args:
            vote_session_id: The ID of the vote session.

        Returns:
            A tuple of (upvotes, downvotes, net_votes).
        """
        votes = await self.get_votes(vote_session_id)
        
        upvotes = sum(vote["weight"] for vote in votes if vote["weight"] > 0)
        downvotes = sum(abs(vote["weight"]) for vote in votes if vote["weight"] < 0)
        net_votes = sum(vote["weight"] for vote in votes)
        
        return upvotes, downvotes, net_votes

    async def track_vote_session_messages(
        self,
        vote_session_id: int,
        message_ids: Iterable[int],
        *,
        build_id: int | None = None,
    ) -> None:
        """Track messages associated with a vote session.

        Args:
            vote_session_id: The ID of the vote session.
            message_ids: The IDs of the messages to track.
            build_id: The ID of the build, if applicable.
        """
        # This would typically call the MessageManager, but for now we'll implement it directly
        # TODO: Consider making this use MessageManager instead
        async with self.session() as session:
            for message_id in message_ids:
                stmt = (
                    pg_insert(Message)
                    .values(
                        id=message_id,
                        purpose="vote",
                        build_id=build_id,
                        vote_session_id=vote_session_id,
                    )
                    .on_conflict_do_update(
                        index_elements=[Message.id],
                        set_=dict(
                            purpose="vote",
                            build_id=build_id,
                            vote_session_id=vote_session_id,
                        )
                    )
                )
                await session.execute(stmt)
            await session.commit()

    async def cleanup_expired_vote_sessions(self, max_age_hours: int = 24) -> int:
        """Clean up expired vote sessions.

        Args:
            max_age_hours: The maximum age in hours before a vote session is considered expired.

        Returns:
            The number of vote sessions that were cleaned up.
        """
        # This is a placeholder for future implementation
        # Would need to add a cleanup mechanism for old vote sessions
        return 0 