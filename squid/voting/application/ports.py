"""Voting application ports."""

from collections.abc import Sequence
from typing import Protocol

from whenever import Instant

from squid.unit_of_work import UnitOfWork
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
    """Calculate a positive base weight, or return None for an ineligible actor.

    VoteService applies the selected option's multiplier to this weight.
    """

    async def calculate(self, actor: VoteActor, session: VoteSessionSnapshot) -> float | None: ...


class VoteActorResolver(Protocol):
    """Resolve membership in the guild whose roles determine ballot weight."""

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Return the account's membership facts in `guild_id`.

        An account that is definitely not a member resolves to a `VoteActor` with
        no roles or capabilities. The policy then determines whether the account is
        still eligible and what weight it receives. `None` means membership could
        not be established; refreshes preserve the ballot's cached weight.
        """
        ...


class InteractiveVoteActorResolver(VoteActorResolver, Protocol):
    """Resolve membership for an interactive transport and surface dependency failure."""

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None: ...

    async def aclose(self) -> None: ...


class VoteRepository(Protocol):
    """Storage operations used by VoteService to manage voting sessions.

    A voting session represents one build review, deletion vote, or poll. Several
    Discord messages can display the same voting session; they share its ballots
    and result. A ballot records one account's selected option and current weight.

    Changes to one voting session must run one at a time. Updating ballots and
    checking whether their net score closes the vote belong to the same transaction,
    so a concurrent request cannot decide against a partly updated score.

    VoteService determines voter eligibility and ballot weights. Repository methods
    store the supplied changes; the surrounding VoteUnitOfWork commits them.
    """

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

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        """Return the session a live Discord card displays."""
        ...

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None: ...

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]: ...

    async def cast_vote(
        self,
        vote_session_id: int,
        account_id: int,
        guild_id: int,
        option_id: str,
        emoji: str,
        desired_weight: float,
        refreshed_weights: dict[int, float] | None = None,
    ) -> StoredVoteMutation | None:
        """Toggle the same option off or replace the ballot, then check thresholds.

        Return None if the session is missing or closed. Supplied refresh weights apply
        before the ballot change; nonpositive refresh values remove existing ballots.
        """
        ...

    async def close(self, vote_session_id: int) -> StoredVoteMutation | None:
        """Close an open session as cancelled; return None if it is missing or already closed."""
        ...

    async def refresh_weights(self, vote_session_id: int, weights: dict[int, float]) -> StoredVoteMutation | None:
        """Update weights and check thresholds in the same transaction.

        Missing accounts in `weights` remain unchanged. Closed sessions remain frozen;
        only a missing session returns None.
        """
        ...

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]: ...

    async def get_emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset | None: ...

    async def set_emoji_preset(self, preset: EmojiPreset) -> None: ...

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]: ...

    async def set_role_weight(self, weight: RoleWeight) -> None: ...

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None: ...

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None: ...


class VoteUnitOfWork(UnitOfWork, Protocol):
    @property
    def votes(self) -> VoteRepository: ...
