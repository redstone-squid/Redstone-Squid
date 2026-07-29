"""Voting application ports."""

from collections.abc import Sequence
from typing import Protocol

from squid.voting.domain import StoredVoteMutation, VoteChange, VoteKindLiteral, VoteOption, VoteSessionSnapshot


class VoteRepository(Protocol):
    """Persistence operations required by :class:`VoteService`."""

    async def create_build_session(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption],
    ) -> int: ...

    async def create_delete_log_session(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption],
    ) -> int: ...

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None: ...

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None: ...

    async def list_open(self, kind: VoteKindLiteral) -> Sequence[VoteSessionSnapshot]: ...

    async def cast_vote(
        self,
        message_id: int,
        user_id: int,
        desired_weight: float,
    ) -> StoredVoteMutation | None: ...
