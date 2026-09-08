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

    async def calculate(self, actor: VoteActor, session: VoteSessionSnapshot, emoji: str) -> float | None:
        """Return the finite positive weight of this ballot, or `None` if the actor may not cast it."""
        ...


class VoteActorResolver(Protocol):
    """Resolve current member facts for refresh operations."""

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Return the account's membership facts in `guild_id`.

        A proven non-member resolves to a `VoteActor` holding no roles or capabilities, which
        weights at the default. `None` means the lookup could not answer — an unreachable or
        invisible guild — and tells callers to keep the weight already recorded.
        """
        ...


class InteractiveVoteActorResolver(VoteActorResolver, Protocol):
    """Resolve membership for an interactive transport and surface dependency failure.

    `aclose` releases the transport, after which every lookup fails.
    """

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Return member facts, or `None` when the member is absent or the guild unreadable.

        A transport failure raises rather than reading as an absence, so callers can tell a denial
        from an outage.
        """
        ...

    async def aclose(self) -> None:
        """Release the transport this resolver owns; later lookups raise instead of resolving."""
        ...


class VoteRepository(Protocol):
    """Persistence for :class:`VoteService`, where `close` stops a session accepting ballots.

    A method returning `StoredVoteMutation | None` answers `None` when there is no such session,
    and `cast_vote` and the two closes also when it is already closed. Every mutation serializes
    per session, so concurrent ballots cannot interleave.
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
    ) -> int:
        """Return the id of the build's initial review session, creating it only if none exists."""
        ...

    async def create_build_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption],
    ) -> int:
        """Create a build session with its target and options in one transaction, returning its id."""
        ...

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
    ) -> int:
        """Create a message-deletion session with its target and options, returning its id."""
        ...

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
    ) -> int:
        """Create a poll carrying no thresholds, returning its id; nothing is posted here."""
        ...

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        """Return the session an unsuppressed vote card belongs to, or `None`."""
        ...

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        """Return the session, carded or not, or `None` when no such session exists."""
        ...

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        """Return every open session of one kind, across all guilds."""
        ...

    async def cast_vote(
        self,
        message_id: int,
        account_id: int,
        guild_id: int,
        option_id: str,
        emoji: str,
        desired_weight: float,
        refreshed_weights: dict[int, float] | None = None,
    ) -> StoredVoteMutation | None:
        """Record one ballot, applying `refreshed_weights` first and closing at a crossed threshold.

        Re-picking the option already selected withdraws the ballot instead of re-recording it.
        """
        ...

    async def close(self, message_id: int) -> StoredVoteMutation | None:
        """Close the carded session as cancelled, ending ballots on it."""
        ...

    async def close_by_id(self, vote_session_id: int) -> StoredVoteMutation | None:
        """Close a session that may have no card, for the deadline scheduler."""
        ...

    async def refresh_weights(self, vote_session_id: int, weights: dict[int, float]) -> StoredVoteMutation | None:
        """Rewrite cached weights per account, dropping ballots weighted at zero or less.

        An open session that crosses a threshold under the new weights closes here.
        """
        ...

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]:
        """Return the open polls whose deadline has passed."""
        ...

    async def get_emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset | None:
        """Return the guild's configured options in position order, or `None` when it has none."""
        ...

    async def set_emoji_preset(self, preset: EmojiPreset) -> None:
        """Replace the guild's options for one kind; sessions already created keep their snapshot."""
        ...

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]:
        """Return the role multipliers this guild applies to one kind."""
        ...

    async def set_role_weight(self, weight: RoleWeight) -> None:
        """Insert or overwrite one role's multiplier."""
        ...

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None:
        """Drop one role's multiplier, silently when it has none."""
        ...

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None:
        """Drop the guild's options and role weights, for one kind or for all of them."""
        ...
