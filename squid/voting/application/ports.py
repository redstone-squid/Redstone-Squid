"""Voting application ports."""

from collections.abc import Sequence
from typing import Protocol

from whenever import Instant

from squid.voting.domain import (
    EmojiPreset,
    PollScope,
    RoleWeight,
    StoredVoteMutation,
    VoteActor,
    VoteChange,
    VoteKind,
    VoteOption,
    VoteSessionSnapshot,
    VoteVisibility,
)


class VoteWeightPolicy(Protocol):
    """Calculate a positive vote magnitude or reject an ineligible actor."""

    async def calculate(self, actor: VoteActor, session: VoteSessionSnapshot, emoji: str) -> float | None: ...


class VoteActorResolver(Protocol):
    """Resolve current member facts for refresh operations."""

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Return the account's membership facts in `guild_id`.

        An account that is definitely not a member resolves to a `VoteActor` with
        no roles or capabilities, which weights at the default. `None` is reserved
        for "cannot answer" — an unreachable or invisible guild — and tells callers
        to keep the weight they already have rather than rewriting it.
        """
        ...


class InteractiveVoteActorResolver(VoteActorResolver, Protocol):
    """Resolve membership for an interactive transport and surface dependency failure."""

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None: ...

    async def aclose(self) -> None: ...


class VoteRepository(Protocol):
    """Persistence operations required by :class:`VoteService`."""

    async def get_or_create_build_submission_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption],
    ) -> int: ...

    async def create_build_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption],
    ) -> int: ...

    async def create_delete_log_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        options: Sequence[VoteOption],
    ) -> int: ...

    async def create_generic_session(
        self,
        *,
        author_account_id: int,
        question: str,
        visibility: VoteVisibility,
        deadline: Instant,
        options: Sequence[VoteOption],
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
    ) -> int: ...

    async def attach_message(self, vote_session_id: int, message_id: int) -> None:
        """Attach an already recorded presentation message, tolerating an exact replay."""
        ...

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None: ...

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None: ...

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]: ...

    async def cast_vote(
        self,
        message_id: int,
        account_id: int,
        guild_id: int,
        option_id: str,
        emoji: str,
        desired_weight: float,
        refreshed_weights: dict[int, float] | None = None,
    ) -> StoredVoteMutation | None: ...

    async def close(self, message_id: int) -> StoredVoteMutation | None: ...

    async def close_by_id(self, vote_session_id: int) -> StoredVoteMutation | None: ...

    async def refresh_weights(self, vote_session_id: int, weights: dict[int, float]) -> StoredVoteMutation | None: ...

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]: ...

    async def get_emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset | None: ...

    async def set_emoji_preset(self, preset: EmojiPreset) -> None: ...

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]: ...

    async def set_role_weight(self, weight: RoleWeight) -> None: ...

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None: ...

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None: ...
