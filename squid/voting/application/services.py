"""Voting application services."""

from collections.abc import Sequence

from squid.voting.application.ports import VoteRepository
from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    CastVoteResult,
    VoteActor,
    VoteChange,
    VoteChoice,
    VoteKindLiteral,
    VoteOption,
    VoteRejection,
    VoteSessionSnapshot,
    normalize_vote_options,
)


class VoteService:
    """Own voting authorization, weights, choices, toggling, and closure policy."""

    def __init__(self, repository: VoteRepository):
        self._repository = repository

    async def start_build_vote(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Create a build vote and its target atomically."""
        options = normalize_vote_options(options)
        return await self._repository.create_build_session(
            author_id=author_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            build_id=build_id,
            changes=changes,
            options=options,
        )

    async def start_delete_log_vote(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption] = DEFAULT_VOTE_OPTIONS,
    ) -> int:
        """Create a message-deletion vote and its target atomically."""
        options = normalize_vote_options(options)
        return await self._repository.create_delete_log_session(
            author_id=author_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            message_id=message_id,
            channel_id=channel_id,
            server_id=server_id,
            options=options,
        )

    async def get_session(self, message_id: int) -> VoteSessionSnapshot | None:
        return await self._repository.get_by_message(message_id)

    async def get_session_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return await self._repository.get_by_id(vote_session_id)

    async def list_open(self, kind: VoteKindLiteral) -> Sequence[VoteSessionSnapshot]:
        return await self._repository.list_open(kind)

    async def cast_vote(self, message_id: int, actor: VoteActor, emoji: str) -> CastVoteResult:
        snapshot = await self._repository.get_by_message(message_id)
        if snapshot is None:
            return CastVoteResult(session=None, rejection="not_found")
        if snapshot.status != "open":
            return CastVoteResult(session=snapshot, rejection="closed")
        if snapshot.kind == "delete_log" and not (actor.is_trusted or actor.is_staff):
            return CastVoteResult(session=snapshot, rejection="not_eligible")

        option = next((option for option in snapshot.options if option.emoji == emoji), None)
        if option is None:
            return CastVoteResult(session=snapshot, rejection="invalid_option")

        weight = option.multiplier * (3.0 if actor.is_staff else 1.0)
        desired_weight = weight if option.choice is VoteChoice.APPROVE else -weight
        mutation = await self._repository.cast_vote(message_id, actor.user_id, desired_weight)
        if mutation is None:
            latest = await self._repository.get_by_message(message_id)
            rejection: VoteRejection = "closed" if latest is not None else "not_found"
            return CastVoteResult(session=latest, rejection=rejection)

        return CastVoteResult(
            session=mutation.session,
            previous_weight=mutation.previous_weight,
            current_weight=mutation.current_weight,
            just_closed=mutation.just_closed,
        )
