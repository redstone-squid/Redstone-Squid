"""Domain models and repository for voting operations.

This module provides abstractions for vote sessions, votes, and related operations
to decouple the bot logic from direct database access.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any, Literal, Protocol

from postgrest.base_request_builder import APIResponse, SingleAPIResponse
from postgrest.types import ReturnMethod

from squid.db.schema import VoteSessionRecord, BuildVoteSessionRecord, DeleteLogVoteSessionRecord


# AIDEV-NOTE: Domain models for voting
class Vote:
    """Represents a single vote in a vote session."""
    
    def __init__(self, user_id: int, weight: float):
        self.user_id = user_id
        self.weight = weight


class VoteSession:
    """Domain model for a vote session."""
    
    def __init__(
        self,
        id: int | None,
        author_id: int,
        kind: str,
        pass_threshold: int,
        fail_threshold: int,
        status: Literal["open", "closed"] = "open",
        votes: dict[int, float] | None = None,
        message_ids: set[int] | None = None,
    ):
        self.id = id
        self.author_id = author_id
        self.kind = kind
        self.pass_threshold = pass_threshold
        self.fail_threshold = fail_threshold
        self.status = status
        self.votes = votes or {}
        self.message_ids = message_ids or set()
    
    @property
    def upvotes(self) -> float:
        """Calculate total upvotes."""
        return sum(weight for weight in self.votes.values() if weight > 0)
    
    @property
    def downvotes(self) -> float:
        """Calculate total downvotes."""
        return sum(weight for weight in self.votes.values() if weight < 0)
    
    @property
    def net_votes(self) -> float:
        """Calculate net votes (upvotes + downvotes)."""
        return sum(self.votes.values())
    
    @property
    def is_closed(self) -> bool:
        """Check if the vote session is closed."""
        return self.status == "closed"


class BuildVoteSession(VoteSession):
    """Domain model for build vote sessions."""
    
    def __init__(
        self,
        id: int | None,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: list[tuple[str, Any, Any]],
        status: Literal["open", "closed"] = "open",
        votes: dict[int, float] | None = None,
        message_ids: set[int] | None = None,
    ):
        super().__init__(id, author_id, "build", pass_threshold, fail_threshold, status, votes, message_ids)
        self.build_id = build_id
        self.changes = changes


class DeleteLogVoteSession(VoteSession):
    """Domain model for delete log vote sessions."""
    
    def __init__(
        self,
        id: int | None,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        target_message_id: int,
        target_channel_id: int,
        target_server_id: int,
        status: Literal["open", "closed"] = "open",
        votes: dict[int, float] | None = None,
        message_ids: set[int] | None = None,
    ):
        super().__init__(id, author_id, "delete_log", pass_threshold, fail_threshold, status, votes, message_ids)
        self.target_message_id = target_message_id
        self.target_channel_id = target_channel_id
        self.target_server_id = target_server_id


# AIDEV-NOTE: Repository interfaces for voting operations
class VoteRepository(Protocol):
    """Protocol for vote repository operations."""
    
    async def upsert_vote(self, vote_session_id: int, user_id: int, weight: float | None) -> None:
        """Upsert a vote for a user in a vote session."""
        ...


class VoteSessionRepository(Protocol):
    """Protocol for vote session repository operations."""
    
    async def create_vote_session(
        self,
        author_id: int,
        kind: str,
        pass_threshold: int,
        fail_threshold: int,
        message_ids: Iterable[int],
        build_id: int | None = None,
    ) -> int:
        """Create a new vote session and return its ID."""
        ...
    
    async def get_vote_session_by_id(self, session_id: int) -> VoteSession | None:
        """Get a vote session by its ID."""
        ...
    
    async def get_vote_session_by_message_id(self, message_id: int) -> VoteSession | None:
        """Get a vote session by a message ID associated with it."""
        ...
    
    async def get_open_vote_sessions_by_kind(self, kind: str) -> list[VoteSession]:
        """Get all open vote sessions of a specific kind."""
        ...
    
    async def close_vote_session(self, session_id: int) -> None:
        """Close a vote session."""
        ...
    
    async def track_message_for_vote_session(
        self,
        message: any,  # discord.Message object  
        vote_session_id: int,
        build_id: int | None = None,
    ) -> None:
        """Track a message as part of a vote session."""
        ...


class BuildVoteSessionRepository(Protocol):
    """Protocol for build vote session specific operations."""
    
    async def create_build_vote_session(
        self,
        vote_session_id: int,
        build_id: int,
        changes: list[tuple[str, Any, Any]],
    ) -> None:
        """Create a build vote session record."""
        ...
    
    async def get_build_vote_session(self, vote_session_id: int) -> BuildVoteSession | None:
        """Get a build vote session by vote session ID."""
        ...


class DeleteLogVoteSessionRepository(Protocol):
    """Protocol for delete log vote session specific operations."""
    
    async def create_delete_log_vote_session(
        self,
        vote_session_id: int,
        target_message_id: int,
        target_channel_id: int,
        target_server_id: int,
    ) -> None:
        """Create a delete log vote session record."""
        ...
    
    async def get_delete_log_vote_session(self, vote_session_id: int) -> DeleteLogVoteSession | None:
        """Get a delete log vote session by vote session ID."""
        ...


# AIDEV-NOTE: Concrete implementations using DatabaseManager
class DatabaseVoteRepository:
    """Database implementation of vote repository."""
    
    def __init__(self, db_manager):
        self._db = db_manager
    
    async def upsert_vote(self, vote_session_id: int, user_id: int, weight: float | None) -> None:
        """Upsert a vote in the database."""
        await (
            self._db.table("votes")
            .upsert(
                {"vote_session_id": vote_session_id, "user_id": user_id, "weight": weight},
                returning=ReturnMethod.minimal,
            )
            .execute()
        )


class DatabaseVoteSessionRepository:
    """Database implementation of vote session repository."""
    
    def __init__(self, db_manager):
        self._db = db_manager
    
    async def create_vote_session(
        self,
        author_id: int,
        kind: str,
        pass_threshold: int,
        fail_threshold: int,
        message_ids: Iterable[int],
        build_id: int | None = None,
    ) -> int:
        """Create a new vote session and return its ID."""
        # Create the vote session
        response: APIResponse[VoteSessionRecord] = (
            await self._db.table("vote_sessions")
            .insert({
                "status": "open",
                "author_id": author_id,
                "kind": kind,
                "pass_threshold": pass_threshold,
                "fail_threshold": fail_threshold,
            })
            .execute()
        )
        session_id = response.data[0]["id"]
        
        # Note: Message tracking will be handled by the service layer
        # since it requires Discord message objects, not just IDs
        
        return session_id
    
    async def get_vote_session_by_id(self, session_id: int) -> VoteSession | None:
        """Get a vote session by its ID."""
        response: SingleAPIResponse[dict[str, Any]] | None = (
            await self._db.table("vote_sessions")
            .select("*, messages(*), votes(*)")
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        
        if response is None:
            return None
        
        record = response.data
        return self._vote_session_from_record(record)
    
    async def get_vote_session_by_message_id(self, message_id: int) -> VoteSession | None:
        """Get a vote session by a message ID associated with it."""
        response: SingleAPIResponse[dict[str, Any]] | None = (
            await self._db.table("messages")
            .select("vote_session_id, vote_sessions(*, messages(*), votes(*))")
            .eq("id", message_id)
            .eq("purpose", "vote")
            .maybe_single()
            .execute()
        )
        
        if response is None:
            return None
        
        vote_session_record = response.data["vote_sessions"]
        return self._vote_session_from_record(vote_session_record)
    
    async def get_open_vote_sessions_by_kind(self, kind: str) -> list[VoteSession]:
        """Get all open vote sessions of a specific kind."""
        records: list[dict[str, Any]] = (
            await self._db.table("vote_sessions")
            .select("*, messages(*), votes(*)")
            .eq("status", "open")
            .eq("kind", kind)
            .execute()
        ).data
        
        return [self._vote_session_from_record(record) for record in records]
    
    async def close_vote_session(self, session_id: int) -> None:
        """Close a vote session."""
        await (
            self._db.table("vote_sessions")
            .update({"status": "closed"}, returning=ReturnMethod.minimal)
            .eq("id", session_id)
            .execute()
        )
    
    async def track_message_for_vote_session(
        self,
        message: any,  # discord.Message object
        vote_session_id: int,
        build_id: int | None = None,
    ) -> None:
        """Track a message as part of a vote session."""
        await self._db.message.track_message(
            message=message,
            purpose="vote",
            build_id=build_id,
            vote_session_id=vote_session_id,
        )
    
    def _vote_session_from_record(self, record: dict[str, Any]) -> VoteSession:
        """Convert database record to VoteSession domain model."""
        votes = {vote["user_id"]: vote["weight"] for vote in record.get("votes", [])}
        message_ids = {msg["id"] for msg in record.get("messages", [])}
        
        return VoteSession(
            id=record["id"],
            author_id=record["author_id"],
            kind=record["kind"],
            pass_threshold=record["pass_threshold"],
            fail_threshold=record["fail_threshold"],
            status=record["status"],
            votes=votes,
            message_ids=message_ids,
        )


class DatabaseBuildVoteSessionRepository:
    """Database implementation of build vote session repository."""
    
    def __init__(self, db_manager):
        self._db = db_manager
    
    async def create_build_vote_session(
        self,
        vote_session_id: int,
        build_id: int,
        changes: list[tuple[str, Any, Any]],
    ) -> None:
        """Create a build vote session record."""
        await (
            self._db.table("build_vote_sessions")
            .insert({
                "vote_session_id": vote_session_id,
                "build_id": build_id,
                "changes": changes,
            }, returning=ReturnMethod.minimal)
            .execute()
        )
    
    async def get_build_vote_session(self, vote_session_id: int) -> BuildVoteSession | None:
        """Get a build vote session by vote session ID."""
        response: SingleAPIResponse[dict[str, Any]] | None = (
            await self._db.table("vote_sessions")
            .select("*, messages(*), votes(*), build_vote_sessions(*)")
            .eq("id", vote_session_id)
            .eq("kind", "build")
            .maybe_single()
            .execute()
        )
        
        if response is None:
            return None
        
        record = response.data
        build_record = record["build_vote_sessions"]
        
        if build_record is None:
            return None
        
        votes = {vote["user_id"]: vote["weight"] for vote in record.get("votes", [])}
        message_ids = {msg["id"] for msg in record.get("messages", [])}
        
        return BuildVoteSession(
            id=record["id"],
            author_id=record["author_id"],
            pass_threshold=record["pass_threshold"],
            fail_threshold=record["fail_threshold"],
            build_id=build_record["build_id"],
            changes=build_record["changes"],
            status=record["status"],
            votes=votes,
            message_ids=message_ids,
        )


class DatabaseDeleteLogVoteSessionRepository:
    """Database implementation of delete log vote session repository."""
    
    def __init__(self, db_manager):
        self._db = db_manager
    
    async def create_delete_log_vote_session(
        self,
        vote_session_id: int,
        target_message_id: int,
        target_channel_id: int,
        target_server_id: int,
    ) -> None:
        """Create a delete log vote session record."""
        await (
            self._db.table("delete_log_vote_sessions")
            .insert({
                "vote_session_id": vote_session_id,
                "target_message_id": target_message_id,
                "target_channel_id": target_channel_id,
                "target_server_id": target_server_id,
            }, returning=ReturnMethod.minimal)
            .execute()
        )
    
    async def get_delete_log_vote_session(self, vote_session_id: int) -> DeleteLogVoteSession | None:
        """Get a delete log vote session by vote session ID."""
        response: SingleAPIResponse[dict[str, Any]] | None = (
            await self._db.table("vote_sessions")
            .select("*, messages(*), votes(*), delete_log_vote_sessions(*)")
            .eq("id", vote_session_id)
            .eq("kind", "delete_log")
            .maybe_single()
            .execute()
        )
        
        if response is None:
            return None
        
        record = response.data
        delete_log_record = record["delete_log_vote_sessions"]
        
        if delete_log_record is None:
            return None
        
        votes = {vote["user_id"]: vote["weight"] for vote in record.get("votes", [])}
        message_ids = {msg["id"] for msg in record.get("messages", [])}
        
        return DeleteLogVoteSession(
            id=record["id"],
            author_id=record["author_id"],
            pass_threshold=record["pass_threshold"],
            fail_threshold=record["fail_threshold"],
            target_message_id=delete_log_record["target_message_id"],
            target_channel_id=delete_log_record["target_channel_id"],
            target_server_id=delete_log_record["target_server_id"],
            status=record["status"],
            votes=votes,
            message_ids=message_ids,
        )


# AIDEV-NOTE: Aggregate repository for all voting operations
class VotingRepository:
    """Aggregate repository for all voting operations."""
    
    def __init__(self, db_manager):
        self.votes = DatabaseVoteRepository(db_manager)
        self.vote_sessions = DatabaseVoteSessionRepository(db_manager)
        self.build_vote_sessions = DatabaseBuildVoteSessionRepository(db_manager)
        self.delete_log_vote_sessions = DatabaseDeleteLogVoteSessionRepository(db_manager) 