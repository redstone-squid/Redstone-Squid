"""Voting application ports."""

from collections.abc import Sequence
from typing import Protocol

from whenever import Instant

from squid.core.actors import UserActor
from squid.core.pagination import Page, PageSelector
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.voting.domain import (
    EmojiPreset,
    PollScope,
    ReviewFilters,
    ReviewSort,
    RoleWeight,
    StoredVoteMutation,
    VoteChange,
    VoteKind,
    VoteOption,
    VoteSessionSnapshot,
    VoteStatus,
    VoteVisibility,
)


class VoteWeightPolicy(Protocol):
    """Calculate a positive base weight, or return None for an ineligible actor.

    VoteService applies the selected option's multiplier to this weight.
    """

    async def calculate(self, actor: UserActor, session: VoteSessionSnapshot) -> float | None: ...


class VoteActorResolver(Protocol):
    """Resolve membership in the guild whose roles determine ballot weight."""

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> UserActor | None:
        """Return the account's membership facts in `guild_id`.

        An account that is definitely not a member resolves to a `UserActor` with
        no roles or capabilities. The policy then determines whether the account is
        still eligible and what weight it receives. `None` means membership could
        not be established.
        """
        ...


class InteractiveVoteActorResolver(VoteActorResolver, Protocol):
    """Resolve membership for an interactive transport and surface dependency failure."""

    async def member(self, account_id: int, guild_id: int, kind: VoteKind) -> UserActor | None: ...


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

    async def list_reviews(
        self, filters: ReviewFilters, *, sort: ReviewSort, selector: PageSelector, page_size: int
    ) -> Page[VoteSessionSnapshot]:
        """Read one page of build review sessions in display order."""
        ...

    async def cast_vote(
        self,
        vote_session_id: int,
        account_id: int,
        guild_id: int,
        option: VoteOption,
        desired_weight: float,
        *,
        toggle: bool = True,
        count_revision: int | None = None,
    ) -> StoredVoteMutation | None:
        """Toggle the same option off or replace the ballot, then check thresholds.

        `option` is the exact alias the ballot uses, as resolved from the session's
        options for `guild_id`. Return None if the session is missing or closed. With `toggle` false,
        selecting the stored option again keeps the ballot instead of removing it.
        """
        ...

    async def clear_vote(self, vote_session_id: int, account_id: int) -> StoredVoteMutation | None:
        """Remove a ballot and check thresholds; return None if the session is missing or closed."""
        ...

    async def close(self, vote_session_id: int) -> StoredVoteMutation | None:
        """Close an open session as cancelled; return None if it is missing or already closed."""
        ...

    async def transition_poll(
        self,
        snapshot: VoteSessionSnapshot,
        status: VoteStatus,
        *,
        retry_at: Instant | None = None,
        unreadable: Sequence[int] = (),
        deadline: Instant | None = None,
        actor_id: int | None = None,
        reason: str = "",
        claim: bool = False,
    ) -> VoteSessionSnapshot | None: ...

    async def pending_reviews(self, guild_id: int) -> Sequence[int]: ...

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]: ...

    async def lock_configuration(self, guild_id: int) -> None: ...

    async def get_emoji_presets(self, guild_ids: Sequence[int], kind: VoteKind) -> dict[int, EmojiPreset]: ...

    async def get_emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset | None: ...

    async def set_emoji_preset(self, preset: EmojiPreset) -> None: ...

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]: ...

    async def set_role_weight(self, weight: RoleWeight) -> None: ...

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None: ...

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None: ...


class VoteUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def votes(self) -> VoteRepository: ...
