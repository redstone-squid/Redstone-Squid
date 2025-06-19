from typing import Any, Iterable, Literal


class VoteSessionRepository:
    """Repository for VoteSession persistence, queries, and vote tracking."""

    async def create_vote_session(
        self,
        kind: str,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int | None = None,
        target_message_id: int | None = None,
        target_channel_id: int | None = None,
        target_server_id: int | None = None,
    ) -> int: ...
    async def close_vote_session(self, vote_session_id: int) -> None: ...
    async def get_vote_session_by_id(self, vote_session_id: int) -> Any: ...
    async def get_vote_session_by_message_id(self, message_id: int, status: str | None = None) -> Any: ...
    async def list_open_vote_sessions(self, kind: str) -> list[Any]: ...
    async def upsert_vote(self, vote_session_id: int, user_id: int, weight: float | None) -> None: ...
    async def associate_message_with_session(
        self, message_id: int, vote_session_id: int, purpose: str, build_id: int | None = None
    ) -> None: ...


class VoteSessionService:
    """Service for VoteSession domain logic and orchestration (build and delete_log kinds)."""

    async def start_build_vote_session(
        self,
        author_id: int,
        messages: Iterable[Any],
        build: Any,
        type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> int: ...
    async def start_delete_log_vote_session(
        self,
        author_id: int,
        messages: Iterable[Any],
        target_message: Any,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> int: ...
    async def close_vote_session(self, vote_session_id: int) -> None: ...
    async def get_vote_session(self, vote_session_id: int) -> Any: ...
    async def get_vote_session_by_message(self, message_id: int, status: str | None = None) -> Any: ...
    async def list_open_vote_sessions(self, kind: str) -> list[Any]: ...
    async def upsert_vote(self, vote_session_id: int, user_id: int, weight: float | None) -> None: ...
    async def get_voting_weight(self, server_id: int | None, user_id: int) -> float: ...
