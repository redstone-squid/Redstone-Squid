from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import inf, nan

import pytest
from whenever import Instant

from squid.core.actors import ActingContext, AnonymousActor, UserActor
from squid.core.errors import AuthenticationError, AuthorizationError
from squid.core.pagination import Page, PageSelector, offset_page
from squid.core.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF
from squid.core.permissions.models import PermissionGrant
from squid.core.voting.application import VoteService
from squid.core.voting.domain import (
    DEFAULT_VOTE_MENU,
    BallotChange,
    DeleteLogVoteTarget,
    EmojiPreset,
    GuildMember,
    PollScope,
    ReviewFilters,
    ReviewKind,
    ReviewSort,
    RoleWeight,
    StoredVoteMutation,
    VoteAlias,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteMenu,
    VoteMessage,
    VoteOption,
    Voter,
    VoteRejection,
    VoteSelection,
    VoteSessionResult,
    VoteSessionSnapshot,
    VoteStatus,
    VoteVisibility,
)
from squid.core.voting.errors import InvalidVoteConfigurationError
from tests.support.voting import (
    DEFAULT_BUILD_TARGET,
    GENERIC_MENU,
    VoteUnitOfWork,
    build_snapshot,
    generic_menu,
    poll_snapshot,
)

STAFF = frozenset({VOTE_WEIGHT_STAFF.name})
DELETE_LOG = frozenset({VOTE_LOG_DELETE_CAST.name})
OWNER_GUILD_ID = 10
"""The guild that owns the default snapshot, matching its message location."""

VOTING_GUILD_ID = 999
"""A guild that hosts a card for a shared session without owning it."""


@dataclass(frozen=True)
class CastVoteCall:
    vote_session_id: int
    account_id: int | None
    guild_id: int | None
    option_id: str
    emoji: str | None
    desired_weight: float


def snapshot(
    *,
    kind: VoteKind = VoteKind.BUILD,
    status: VoteStatus = VoteStatus.OPEN,
    result: VoteSessionResult = VoteSessionResult.PENDING,
    votes: dict[int, float] | None = None,
) -> VoteSessionSnapshot:
    # A delete-log session is owned by the server holding the message it targets,
    # which is also where its card lives.
    target = (
        DeleteLogVoteTarget(message_id=501, channel_id=200, server_id=OWNER_GUILD_ID)
        if kind is VoteKind.DELETE_LOG
        else DEFAULT_BUILD_TARGET
    )
    return build_snapshot(kind=kind, status=status, result=result, votes=votes, target=target)


def shared_snapshot() -> VoteSessionSnapshot:
    """A build review with cards in both the owner guild and another guild."""
    return replace(
        snapshot(),
        messages=(VoteMessage(100, 200, OWNER_GUILD_ID), VoteMessage(101, 201, VOTING_GUILD_ID)),
    )


class FakeVoteRepository:
    async def lock_configuration(self, guild_id: int) -> None:
        pass

    """Record service calls and return configured snapshots and mutations without applying votes."""

    def __init__(self, session: VoteSessionSnapshot | None):
        self.session = session
        self.cast_calls: list[CastVoteCall] = []
        self.clear_calls: list[tuple[int, Voter, BallotChange]] = []
        self.members: dict[tuple[int, int], GuildMember] = {}
        """Stored memberships by guild and account."""
        self.member_lookups: list[tuple[int, int]] = []
        self.mutation: StoredVoteMutation | None = None
        self.role_weights: dict[tuple[int, VoteKind], list[RoleWeight]] = {}
        self.role_weight_lookups: list[tuple[int, VoteKind]] = []
        self.due: list[VoteSessionSnapshot] = []
        self.generic_scopes: list[PollScope] = []
        self.build_create_calls: list[tuple[int, int, int, int, list[VoteChange], VoteMenu]] = []
        self.delete_create_calls: list[tuple[int, int, int, int, int, int, VoteMenu]] = []
        self.generic_create_calls: list[tuple[int, str, VoteVisibility, Instant, int | None]] = []
        self.attach_calls: list[tuple[int, int]] = []

    async def get_or_create_build_submission_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        menu: VoteMenu,
    ) -> int:
        self.build_create_calls.append(
            (author_account_id, pass_threshold, fail_threshold, build_id, list(changes), menu)
        )
        return 23

    async def create_generic_session(
        self,
        *,
        author_account_id: int,
        question: str,
        visibility: VoteVisibility,
        deadline: Instant,
        menu: VoteMenu,
        guild_id: int | None = None,
        scope: PollScope = PollScope.GUILD,
    ) -> int:
        self.generic_create_calls.append((author_account_id, question, visibility, deadline, guild_id))
        self.generic_scopes.append(scope)
        return 26

    async def attach_message(self, vote_session_id: int, message_id: int) -> None:
        # Replace the list so the fake UOW only publishes the attachment on commit.
        self.attach_calls = [*self.attach_calls, (vote_session_id, message_id)]

    async def create_build_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        menu: VoteMenu,
    ) -> int:
        self.build_create_calls.append(
            (author_account_id, pass_threshold, fail_threshold, build_id, list(changes), menu)
        )
        return 24

    async def create_delete_log_session(
        self,
        *,
        author_account_id: int,
        pass_threshold: int,
        fail_threshold: int,
        message_id: int,
        channel_id: int,
        server_id: int,
        menu: VoteMenu,
    ) -> int:
        self.delete_create_calls.append(
            (author_account_id, pass_threshold, fail_threshold, message_id, channel_id, server_id, menu)
        )
        return 25

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        return self.session

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return self.session

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        return [] if self.session is None or self.session.kind is not kind else [self.session]

    async def list_reviews(
        self, filters: ReviewFilters, *, sort: ReviewSort, selector: PageSelector, page_size: int
    ) -> Page[VoteSessionSnapshot]:
        session = self.session
        visible = (
            session is not None
            and session.kind in filters.vote_kinds
            and all(s is session.status for s in filters.statuses)
        )
        return offset_page([session] if visible and session else [], offset=selector.offset, page_size=page_size)

    async def cast_vote(
        self,
        vote_session_id: int,
        voter: Voter,
        option: VoteOption,
        weight: float,
        *,
        guild_id: int | None = None,
        emoji: str | None = None,
        toggle: bool = True,
        count_revision: int | None = None,
    ) -> StoredVoteMutation | None:
        self.cast_calls.append(
            CastVoteCall(
                vote_session_id=vote_session_id,
                account_id=voter.account_id,
                guild_id=guild_id,
                option_id=option.id,
                emoji=emoji,
                desired_weight=weight,
            )
        )
        return self.mutation

    async def clear_vote(
        self, vote_session_id: int, voter: Voter, *, change: BallotChange = BallotChange.CLEAR
    ) -> StoredVoteMutation | None:
        self.clear_calls.append((vote_session_id, voter, change))
        return self.mutation

    async def close(self, vote_session_id: int) -> StoredVoteMutation | None:
        return self.mutation

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
    ) -> VoteSessionSnapshot | None:
        if self.session != snapshot or snapshot.poll is None:
            return None
        poll = snapshot.poll
        self.session = replace(
            snapshot,
            status=status,
            poll=replace(
                poll,
                count_revision=poll.count_revision + 1,
                count_attempts=poll.count_attempts + int(claim),
                unreadable_cards=tuple(unreadable),
                retry_at=retry_at,
            ),
        )
        return self.session

    async def pending_reviews(self, guild_id: int) -> Sequence[int]:
        return []

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]:
        return self.due

    async def get_emoji_presets(self, guild_ids: Sequence[int], kind: VoteKind) -> dict[int, EmojiPreset]:
        return {}

    async def get_emoji_preset(self, guild_id: int, kind: VoteKind) -> EmojiPreset | None:
        return None

    async def set_emoji_preset(self, preset: EmojiPreset) -> None:
        pass

    async def get_role_weights(self, guild_id: int, kind: VoteKind) -> Sequence[RoleWeight]:
        self.role_weight_lookups.append((guild_id, kind))
        return self.role_weights.get((guild_id, kind), [])

    async def set_role_weight(self, weight: RoleWeight) -> None:
        pass

    async def remove_role_weight(self, guild_id: int, kind: VoteKind, role_id: int) -> None:
        pass

    async def reset_configuration(self, guild_id: int, kind: VoteKind | None = None) -> None:
        pass

    async def get_member(self, guild_id: int, account_id: int) -> GuildMember | None:
        self.member_lookups.append((guild_id, account_id))
        return self.members.get((guild_id, account_id))

    async def record_members(self, guild_id: int, members: Sequence[GuildMember], *, complete: bool) -> None:
        pass

    async def forget_member(self, guild_id: int, discord_user_id: int) -> None:
        pass


def vote_grants(*nodes: str, account_id: int = 7) -> tuple[PermissionGrant, ...]:
    return tuple(PermissionGrant(pattern=node, effect=1, subject_account_id=account_id) for node in nodes)


@pytest.mark.parametrize("scoped_out", [True, False])
async def test_limited_credentials_and_a_revoked_cast_grant_cannot_change_ballots(scoped_out: bool) -> None:
    initial = snapshot()
    repository = FakeVoteRepository(initial)
    grants = () if scoped_out else (PermissionGrant(pattern="vote.poll.cast", effect=-1, subject_account_id=7),)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository, grants=grants))
    context = ActingContext.direct(
        UserActor(
            account_id=7,
            guild_id=OWNER_GUILD_ID,
            credential_scopes=frozenset({"vote.poll.create"}) if scoped_out else None,
            capabilities=frozenset({"vote.poll.cast"}),
        )
    )
    cast = await service.cast_vote(initial.id, "approve", context=context)
    cleared = await service.clear_vote(initial.id, context=context)
    assert cast.rejection is VoteRejection.NOT_ELIGIBLE
    assert cleared.rejection is VoteRejection.NOT_ELIGIBLE
    assert repository.cast_calls == []


async def test_vote_creation_delegates_complete_aggregate_to_repository() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(
        uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.poll.create", account_id=8))
    )
    changes: list[VoteChange] = [("status", "pending", "confirmed")]

    build_session_id = await service.start_build_vote(
        author_account_id=7,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=changes,
        context=ActingContext.worker("submission-review"),
    )
    delete_session_id = await service.start_delete_log_vote(
        pass_threshold=4,
        fail_threshold=-2,
        message_id=100,
        channel_id=200,
        server_id=300,
        context=ActingContext.direct(UserActor(account_id=8, guild_id=300)),
    )

    assert build_session_id == 24
    assert delete_session_id == 25
    assert repository.build_create_calls == [(7, 3, -3, 42, changes, DEFAULT_VOTE_MENU)]
    assert repository.delete_create_calls == [(8, 4, -2, 100, 200, 300, DEFAULT_VOTE_MENU)]


async def test_initial_build_vote_creation_uses_idempotent_repository_operation() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))
    changes: list[VoteChange] = [("submission_status", 0, 1)]

    session_id = await service.ensure_build_submission_vote(
        author_account_id=7,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=changes,
        context=ActingContext.worker("submission-review"),
    )

    assert session_id == 23
    assert repository.build_create_calls == [(7, 3, -3, 42, changes, DEFAULT_VOTE_MENU)]


async def test_message_attachment_is_committed() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    await service.attach_message(26, 9001, context=ActingContext.direct(AnonymousActor()))

    assert repository.attach_calls == [(26, 9001)]


@pytest.mark.parametrize(
    ("emoji", "has_staff_weight", "expected_weight"),
    [
        ("👍", False, 1.0),
        ("👎", False, -1.0),
        ("👍", True, 3.0),
        ("👎", True, -3.0),
    ],
)
async def test_cast_vote_applies_choice_and_staff_weight(
    emoji: str,
    has_staff_weight: bool,
    expected_weight: float,
) -> None:
    initial = snapshot()
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=initial,
        previous_weight=None,
        current_weight=expected_weight,
        just_closed=False,
    )
    service = VoteService(
        build_owner_guild_id=OWNER_GUILD_ID,
        uow_factory=lambda: VoteUnitOfWork(
            repository, grants=vote_grants("vote.weight.staff") if has_staff_weight else ()
        ),
    )
    option_id = "approve" if emoji == "👍" else "deny"

    result = await service.cast_vote(
        12,
        option_id,
        emoji=emoji,
        context=ActingContext.for_actor(
            UserActor(
                account_id=7,
                discord_user_id=70,
                guild_id=OWNER_GUILD_ID,
                capabilities=STAFF if has_staff_weight else frozenset(),
            )
        ),
    )

    assert result.accepted
    assert repository.cast_calls == [
        CastVoteCall(
            vote_session_id=12,
            account_id=7,
            guild_id=10,
            option_id=option_id,
            emoji=emoji,
            desired_weight=abs(expected_weight),
        )
    ]


@pytest.mark.parametrize(
    ("guild_id", "emoji"),
    [(OWNER_GUILD_ID, "<:yes:1>"), (VOTING_GUILD_ID, "✅")],
)
async def test_cast_vote_resolves_a_stable_option_to_each_guild_alias(guild_id: int, emoji: str) -> None:
    menu = replace(
        DEFAULT_VOTE_MENU,
        aliases=(
            VoteAlias("approve", "<:yes:1>", OWNER_GUILD_ID),
            VoteAlias("deny", "<:no:2>", OWNER_GUILD_ID, 1),
            VoteAlias("approve", "✅", VOTING_GUILD_ID),
            VoteAlias("deny", "❌", VOTING_GUILD_ID, 1),
        ),
    )
    initial = replace(shared_snapshot(), menu=menu)
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=initial,
        previous_weight=None,
        current_weight=1.0,
        just_closed=False,
    )
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12, "approve", context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=guild_id))
    )

    assert result.accepted
    assert repository.cast_calls == [
        CastVoteCall(
            vote_session_id=12,
            account_id=7,
            guild_id=guild_id,
            option_id="approve",
            emoji=emoji,
            desired_weight=1.0,
        )
    ]


async def test_cast_vote_rejects_missing_session() -> None:
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(FakeVoteRepository(None)))

    result = await service.cast_vote(
        404, "approve", context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=10))
    )

    assert result.rejection is VoteRejection.NOT_FOUND
    assert result.session is None


async def test_cast_vote_rejects_a_guild_the_session_is_not_published_in() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12, "approve", context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=999))
    )

    assert result.rejection is VoteRejection.WRONG_GUILD
    assert repository.cast_calls == []


async def test_cast_vote_rejects_unknown_option_identifier() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12, "missing", context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=10))
    )

    assert result.rejection is VoteRejection.INVALID_OPTION
    assert repository.cast_calls == []


async def test_delete_log_vote_requires_the_delete_log_capability() -> None:
    repository = FakeVoteRepository(snapshot(kind=VoteKind.DELETE_LOG))
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="👍",
        context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID)),
    )

    assert result.rejection is VoteRejection.NOT_ELIGIBLE
    assert repository.cast_calls == []


async def test_the_delete_log_capability_admits_a_voter_and_staff_weight_still_applies() -> None:
    initial = snapshot(kind=VoteKind.DELETE_LOG)
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=initial,
        previous_weight=None,
        current_weight=3.0,
        just_closed=False,
    )
    service = VoteService(
        uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.log_delete.cast", "vote.weight.staff"))
    )

    result = await service.cast_vote(
        12,
        "approve",
        emoji="👍",
        context=ActingContext.for_actor(
            UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID, capabilities=STAFF | DELETE_LOG)
        ),
    )

    assert result.accepted
    assert repository.cast_calls == [
        CastVoteCall(
            vote_session_id=12,
            account_id=7,
            guild_id=10,
            option_id="approve",
            emoji="👍",
            desired_weight=3.0,
        )
    ]


async def test_closed_vote_is_rejected_before_mutation() -> None:
    repository = FakeVoteRepository(snapshot(status=VoteStatus.CLOSED, result=VoteSessionResult.APPROVED))
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="👍",
        context=ActingContext.for_actor(
            UserActor(account_id=7, discord_user_id=70, capabilities=DELETE_LOG, guild_id=OWNER_GUILD_ID)
        ),
    )

    assert result.rejection is VoteRejection.CLOSED
    assert repository.cast_calls == []


async def test_atomic_closure_result_is_exposed_to_adapter_once() -> None:
    initial = snapshot(votes={2: 2.0})
    closed = replace(
        initial,
        status=VoteStatus.CLOSED,
        result=VoteSessionResult.APPROVED,
        votes={Voter.account(2): 2.0, Voter.account(7): 1.0},
    )
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=closed,
        previous_weight=None,
        current_weight=1.0,
        just_closed=True,
    )
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="👍",
        context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID)),
    )

    assert result.just_closed
    assert result.session == closed
    assert result.session is not None
    assert result.session.net_votes == 3


async def test_race_with_another_closing_vote_returns_closed_rejection() -> None:
    """Simulate closure between the initial read and mutation, then check the service's response."""
    initial = snapshot()
    repository = FakeVoteRepository(initial)
    repository.mutation = None
    repository.session = initial
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    async def get_after_mutation(vote_session_id: int) -> VoteSessionSnapshot:
        if repository.cast_calls:
            return replace(initial, status=VoteStatus.CLOSED, result=VoteSessionResult.DENIED)
        return initial

    repository.get_by_id = get_after_mutation  # type: ignore[method-assign]
    result = await service.cast_vote(
        12,
        "deny",
        emoji="👎",
        context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID)),
    )

    assert result.rejection is VoteRejection.CLOSED
    assert not result.just_closed


async def test_option_multiplier_scales_the_staff_weight() -> None:
    menu = VoteMenu(
        (VoteOption("strong_approve", VoteChoice.APPROVE, multiplier=2.0), VoteOption("deny", VoteChoice.DENY)),
        (VoteAlias("strong_approve", "<:strong_yes:123>"), VoteAlias("deny", "👎", position=1)),
    )
    initial = replace(snapshot(), menu=menu)
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=initial,
        previous_weight=None,
        current_weight=6.0,
        just_closed=False,
    )
    service = VoteService(
        build_owner_guild_id=OWNER_GUILD_ID,
        uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.weight.staff")),
    )

    result = await service.cast_vote(
        12,
        "strong_approve",
        emoji="<:strong_yes:123>",
        context=ActingContext.for_actor(
            UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID, capabilities=STAFF)
        ),
    )

    assert result.accepted
    assert repository.cast_calls == [
        CastVoteCall(
            vote_session_id=12,
            account_id=7,
            guild_id=10,
            option_id="strong_approve",
            emoji="<:strong_yes:123>",
            desired_weight=6.0,
        )
    ]


async def test_unconfigured_emoji_is_rejected_without_mutation() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="🤷",
        context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID)),
    )

    assert result.rejection is VoteRejection.INVALID_OPTION
    assert repository.cast_calls == []


async def test_vote_options_require_unique_emojis_and_both_choices() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    with pytest.raises(InvalidVoteConfigurationError, match="unique"):
        await service.start_build_vote(
            author_account_id=7,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=42,
            changes=[],
            menu=VoteMenu(DEFAULT_VOTE_MENU.options, (VoteAlias("approve", "👍"), VoteAlias("deny", "👍", position=1))),
            context=ActingContext.worker("submission-review"),
        )

    with pytest.raises(InvalidVoteConfigurationError, match="approve and one deny"):
        await service.start_build_vote(
            author_account_id=7,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=42,
            changes=[],
            menu=VoteMenu((VoteOption("approve", VoteChoice.APPROVE),), (VoteAlias("approve", "👍"),)),
            context=ActingContext.worker("submission-review"),
        )


@pytest.mark.parametrize("multiplier", [0.0, -1.0, inf, nan])
def test_vote_option_rejects_non_positive_or_non_finite_multiplier(multiplier: float) -> None:
    with pytest.raises(InvalidVoteConfigurationError, match="finite and greater than zero"):
        VoteOption("approve", VoteChoice.APPROVE, multiplier=multiplier)


async def test_poll_creation_needs_no_guild_and_no_publication_target() -> None:
    """A poll is a database row before it is a Discord message."""
    repository = FakeVoteRepository(None)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.poll.create")))
    session_id = await service.create_generic_poll(
        context=ActingContext.direct(UserActor(account_id=7)),
        question="  Which one?  ",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        duration_seconds=3600,
        menu=generic_menu(None),
    )

    assert session_id == 26
    author, question, visibility, _deadline, guild_id = repository.generic_create_calls[0]
    assert (author, question, visibility, guild_id) == (7, "Which one?", VoteVisibility.ANONYMOUS_LIVE, None)


@pytest.mark.parametrize("duration_seconds", [59, 30 * 86400 + 1])
async def test_poll_creation_rejects_durations_outside_the_supported_range(duration_seconds: int) -> None:
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(FakeVoteRepository(None)))

    with pytest.raises(InvalidVoteConfigurationError, match="between 1 minute and 30 days"):
        await service.create_generic_poll(
            context=ActingContext.direct(UserActor(account_id=7)),
            question="Which one?",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=duration_seconds,
            menu=generic_menu(None),
        )


async def test_poll_creation_rejects_an_empty_question() -> None:
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(FakeVoteRepository(None)))

    with pytest.raises(InvalidVoteConfigurationError, match="question cannot be empty"):
        await service.create_generic_poll(
            context=ActingContext.direct(UserActor(account_id=7)),
            question="   ",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=3600,
            menu=generic_menu(None),
        )


async def test_closing_a_poll_requires_the_creator_or_the_close_any_capability() -> None:
    poll = poll_snapshot(id=12, author_account_id=7, guild_id=10, messages=(VoteMessage(100, 200, 10),))
    repository = FakeVoteRepository(poll)
    repository.mutation = StoredVoteMutation(
        session=replace(poll, status=VoteStatus.CLOSED, result=VoteSessionResult.CANCELLED),
        previous_weight=None,
        current_weight=None,
        just_closed=True,
    )
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))

    stranger = await service.close(
        12, context=ActingContext.for_actor(UserActor(account_id=8, discord_user_id=80, guild_id=10))
    )

    async def recount(session: int, revision: int) -> tuple[bool, tuple[int, ...]]:
        return False, ()

    creator = await service.close(
        12, recount=recount, context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=10))
    )

    assert stranger.rejection is VoteRejection.NOT_AUTHORIZED
    assert creator.accepted
    assert creator.just_closed


async def test_build_vote_weighs_by_the_owner_guild_not_the_voting_guild() -> None:
    initial = shared_snapshot()
    repository = FakeVoteRepository(initial)
    repository.role_weights = {
        (OWNER_GUILD_ID, VoteKind.BUILD): [RoleWeight(OWNER_GUILD_ID, VoteKind.BUILD, 55, 4.0)],
        # A guild hosting a card must not be able to weight the shared outcome.
        (VOTING_GUILD_ID, VoteKind.BUILD): [RoleWeight(VOTING_GUILD_ID, VoteKind.BUILD, 77, 100.0)],
    }
    repository.members = {(OWNER_GUILD_ID, 7): GuildMember(70, frozenset({55}))}
    repository.mutation = StoredVoteMutation(initial, None, 4.0, just_closed=False)
    service = VoteService(build_owner_guild_id=OWNER_GUILD_ID, uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="✅",
        context=ActingContext.for_actor(
            UserActor(account_id=7, discord_user_id=70, guild_id=VOTING_GUILD_ID, discord_role_ids=frozenset({77}))
        ),
    )

    assert result.accepted
    assert repository.member_lookups == [(OWNER_GUILD_ID, 7)]
    assert repository.role_weight_lookups == [(OWNER_GUILD_ID, VoteKind.BUILD)]
    assert repository.cast_calls[0].desired_weight == 4.0


async def test_the_live_actor_lends_no_authority_to_the_weight() -> None:
    """A voter the owner guild does not list weighs as a member without roles, whatever the card guild says."""
    initial = shared_snapshot()
    repository = FakeVoteRepository(initial)
    repository.role_weights = {
        (VOTING_GUILD_ID, VoteKind.BUILD): [RoleWeight(VOTING_GUILD_ID, VoteKind.BUILD, 77, 100.0)]
    }
    repository.mutation = StoredVoteMutation(initial, None, 1.0, just_closed=False)
    service = VoteService(build_owner_guild_id=OWNER_GUILD_ID, uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="✅",
        context=ActingContext.for_actor(
            UserActor(
                account_id=7,
                discord_user_id=70,
                guild_id=VOTING_GUILD_ID,
                discord_role_ids=frozenset({77}),
                capabilities=STAFF,
                is_bot_owner=True,
                discord_guild_admin=True,
            )
        ),
    )

    assert result.accepted
    assert repository.cast_calls[0].desired_weight == 1.0


async def test_without_an_owner_guild_no_role_table_may_weight_a_build_vote() -> None:
    initial = snapshot()
    repository = FakeVoteRepository(initial)
    repository.members = {(OWNER_GUILD_ID, 7): GuildMember(70, frozenset({55}), manage_server=True)}
    repository.mutation = StoredVoteMutation(initial, None, 1.0, just_closed=False)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.weight.staff")))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="👍",
        context=ActingContext.for_actor(
            UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID, capabilities=STAFF)
        ),
    )

    assert result.accepted
    assert repository.cast_calls[0].desired_weight == 1.0
    assert repository.member_lookups == []


async def test_a_web_ballot_selects_the_option_without_a_card() -> None:
    initial = snapshot()
    repository = FakeVoteRepository(initial)
    repository.members = {(OWNER_GUILD_ID, 7): GuildMember(70)}
    repository.mutation = StoredVoteMutation(initial, None, 3.0, just_closed=False)
    service = VoteService(
        build_owner_guild_id=OWNER_GUILD_ID,
        uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.weight.staff")),
    )

    result = await service.cast_vote(12, "deny", context=ActingContext.for_actor(UserActor(account_id=7)))

    assert result.accepted
    assert repository.cast_calls == [CastVoteCall(12, 7, None, "deny", None, 3.0)]


async def test_a_server_poll_is_voted_on_its_own_cards_by_the_roles_reacted_with() -> None:
    poll = poll_snapshot(guild_id=OWNER_GUILD_ID, messages=(VoteMessage(100, 200, OWNER_GUILD_ID),))
    repository = FakeVoteRepository(poll)
    repository.role_weights = {
        (OWNER_GUILD_ID, VoteKind.GENERIC): [RoleWeight(OWNER_GUILD_ID, VoteKind.GENERIC, 55, 2.0)]
    }
    repository.mutation = StoredVoteMutation(poll, None, 2.0, just_closed=False)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))
    reactor = ActingContext.for_actor(
        UserActor(account_id=7, discord_user_id=70, guild_id=OWNER_GUILD_ID, discord_role_ids=frozenset({55}))
    )
    web_voter = ActingContext.for_actor(UserActor(account_id=7))

    accepted = await service.cast_vote(poll.id, "one", emoji="1️⃣", context=reactor)
    refused = await service.cast_vote(poll.id, "one", context=web_voter)

    assert accepted.accepted
    assert repository.cast_calls[0].desired_weight == 2.0
    assert repository.member_lookups == []
    assert refused.rejection is VoteRejection.WRONG_GUILD
    assert await service.base_weight(poll.id, context=web_voter) is VoteRejection.WRONG_GUILD


async def test_casting_weighs_only_the_new_ballot() -> None:
    """Earlier ballots keep the weight they were cast with, whatever their voters' roles are now."""
    selections = (
        VoteSelection(voter=Voter.account(8), guild_id=OWNER_GUILD_ID, option_id="approve", emoji="👍", weight=4.0),
    )
    initial = replace(shared_snapshot(), selections=selections, votes={Voter.account(8): 4.0})
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(initial, None, 1.0, just_closed=False)
    service = VoteService(build_owner_guild_id=OWNER_GUILD_ID, uow_factory=lambda: VoteUnitOfWork(repository))

    result = await service.cast_vote(
        12,
        "approve",
        emoji="✅",
        context=ActingContext.for_actor(UserActor(account_id=7, discord_user_id=70, guild_id=VOTING_GUILD_ID)),
    )

    assert result.accepted
    assert repository.member_lookups == [(OWNER_GUILD_ID, 7)]


@pytest.mark.parametrize(("guild_id", "voided"), [(OWNER_GUILD_ID, True), (VOTING_GUILD_ID, False)])
async def test_a_ban_voids_ballots_only_in_the_sessions_that_guild_weighs(guild_id: int, voided: bool) -> None:
    selections = (
        VoteSelection(voter=Voter.account(7), guild_id=VOTING_GUILD_ID, option_id="approve", emoji="👍", weight=4.0),
    )
    initial = replace(shared_snapshot(), selections=selections, votes={Voter.account(7): 4.0})
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(replace(initial, selections=(), votes={}), 4.0, None, just_closed=False)
    service = VoteService(build_owner_guild_id=OWNER_GUILD_ID, uow_factory=lambda: VoteUnitOfWork(repository))

    changed = await service.void_ballots(Voter.account(7), guild_id, context=ActingContext.worker("discord.ban"))

    assert repository.clear_calls == ([(12, Voter.account(7), BallotChange.VOID)] if voided else [])
    assert [session.id for session in changed] == ([12] if voided else [])


async def test_each_kind_names_the_guild_that_owns_its_weights() -> None:
    service = VoteService(
        build_owner_guild_id=OWNER_GUILD_ID, uow_factory=lambda: VoteUnitOfWork(FakeVoteRepository(None))
    )
    poll = poll_snapshot(guild_id=77)
    assert poll.poll is not None

    delete_log = replace(
        snapshot(kind=VoteKind.DELETE_LOG),
        target=DeleteLogVoteTarget(message_id=501, channel_id=200, server_id=64),
    )

    assert service._owner_guild_id(snapshot()) == OWNER_GUILD_ID
    assert service._owner_guild_id(delete_log) == 64
    assert service._owner_guild_id(poll) == 77
    assert service._owner_guild_id(replace(poll, poll=replace(poll.poll, guild_id=None))) is None
    assert VoteService(uow_factory=lambda: VoteUnitOfWork(FakeVoteRepository(None)))._owner_guild_id(snapshot()) is None


async def test_a_network_poll_belongs_to_no_guild_and_needs_unscoped_options() -> None:
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(FakeVoteRepository(None)))
    creator = ActingContext.direct(UserActor(account_id=7, guild_id=10))

    with pytest.raises(InvalidVoteConfigurationError, match="belongs to no guild"):
        await service.create_generic_poll(
            context=creator,
            question="Which?",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=3600,
            menu=generic_menu(None),
            guild_id=10,
            scope=PollScope.NETWORK,
        )
    with pytest.raises(InvalidVoteConfigurationError, match="not be scoped to one guild"):
        await service.create_generic_poll(
            context=creator,
            question="Which?",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=3600,
            menu=GENERIC_MENU,
            scope=PollScope.NETWORK,
        )


async def test_a_network_poll_needs_a_network_grant_not_a_server_one() -> None:
    repository = FakeVoteRepository(None)
    server_grant = PermissionGrant(pattern="vote.network.create", effect=1, subject_account_id=7, scope_guild_id=10)
    service = VoteService(
        uow_factory=lambda: VoteUnitOfWork(repository, grants=(*vote_grants("vote.poll.create"), server_grant))
    )

    with pytest.raises(AuthorizationError):
        await service.create_generic_poll(
            context=ActingContext.direct(UserActor(account_id=7, guild_id=10)),
            question="Which?",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=3600,
            menu=generic_menu(None),
            scope=PollScope.NETWORK,
        )
    assert repository.generic_scopes == []


async def test_a_network_poll_records_its_scope() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(
        uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.poll.create", "vote.network.create"))
    )

    await service.create_generic_poll(
        context=ActingContext.direct(UserActor(account_id=7, guild_id=10)),
        question="Which?",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        duration_seconds=3600,
        menu=generic_menu(None),
        scope=PollScope.NETWORK,
    )

    assert repository.generic_scopes == [PollScope.NETWORK]


async def test_a_network_poll_weighs_every_ballot_one() -> None:
    poll = poll_snapshot(guild_id=None, scope=PollScope.NETWORK, menu=generic_menu(None))
    repository = FakeVoteRepository(poll)
    repository.members = {(10, 7): GuildMember(70, frozenset({55}), manage_server=True)}
    repository.mutation = StoredVoteMutation(poll, None, 1.0, just_closed=False)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.weight.staff")))

    result = await service.cast_vote(poll.id, "one", context=ActingContext.for_actor(UserActor(account_id=7)))

    assert result.accepted
    assert repository.cast_calls[0].desired_weight == 1.0
    assert repository.member_lookups == []


@pytest.mark.parametrize(
    ("capabilities", "author", "rejection"),
    [
        (frozenset(), True, None),
        (frozenset(), False, VoteRejection.NOT_AUTHORIZED),
        (frozenset({"vote.poll.close_any"}), False, VoteRejection.NOT_AUTHORIZED),
        (frozenset({"vote.network.close_any"}), False, None),
    ],
)
def test_only_the_author_or_network_staff_close_a_network_poll(
    capabilities: frozenset[str], author: bool, rejection: VoteRejection | None
) -> None:
    poll = poll_snapshot(author_account_id=2, guild_id=None, scope=PollScope.NETWORK, menu=generic_menu(None))
    actor = UserActor(account_id=2 if author else 3, guild_id=999, capabilities=capabilities)

    assert poll.can_close(actor) is rejection
    assert poll.can_review_poll(actor, recount_only=False) is ("vote.network.close_any" in capabilities)


@pytest.mark.parametrize("recount_fails", [False, True])
async def test_a_due_poll_is_recounted_before_it_closes_only_if_the_recount_succeeds(recount_fails: bool) -> None:
    selections = (VoteSelection(voter=Voter.account(7), guild_id=77, option_id="one", emoji="1️⃣", weight=9.0),)
    poll = replace(poll_snapshot(guild_id=77), selections=selections)
    repository = FakeVoteRepository(poll)
    repository.due = [poll]
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository))
    recounted: list[int] = []

    async def recount(vote_session_id: int, revision: int) -> tuple[bool, tuple[int, ...]]:
        recounted.append(vote_session_id)
        if recount_fails:
            raise RuntimeError("Discord is down")
        return False, ()

    await service.close_due(recount=recount, context=ActingContext.worker("test-worker"))

    assert recounted == [poll.id]
    assert repository.session is not None
    assert repository.session.status is (VoteStatus.COUNTING if recount_fails else VoteStatus.CLOSED)


async def test_a_discord_user_without_an_account_can_vote_but_not_close() -> None:
    initial = shared_snapshot()
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(initial, None, 1.0, just_closed=False)
    service = VoteService(build_owner_guild_id=OWNER_GUILD_ID, uow_factory=lambda: VoteUnitOfWork(repository))
    actor = UserActor(discord_user_id=70, guild_id=OWNER_GUILD_ID, discord_role_ids=frozenset({55}))
    repository.role_weights = {(OWNER_GUILD_ID, VoteKind.BUILD): [RoleWeight(OWNER_GUILD_ID, VoteKind.BUILD, 55, 2.0)]}

    result = await service.cast_vote(12, "approve", emoji="👍", context=ActingContext.for_actor(actor))
    with pytest.raises(AuthenticationError):
        await service.close(12, context=ActingContext.for_actor(actor))

    assert result.accepted
    # Weighed from the roles they reacted with, since nothing is stored for them.
    assert repository.cast_calls == [CastVoteCall(12, None, OWNER_GUILD_ID, "approve", "👍", 2.0)]


async def test_failed_final_counts_pause_for_review_and_finalize_uses_collected_ballots() -> None:
    poll = poll_snapshot(messages=(VoteMessage(100, 200, 10),))
    repository = FakeVoteRepository(poll)
    service = VoteService(
        uow_factory=lambda: VoteUnitOfWork(
            repository, grants=(*vote_grants("vote.poll.close_any"), *vote_grants("vote.poll.recount", account_id=8))
        )
    )
    attempts: list[int] = []

    async def recount(session_id: int, revision: int) -> tuple[bool, tuple[int, ...]]:
        attempts.append(revision)
        return False, (100,)

    for _ in range(3):
        assert repository.session is not None
        repository.due = [repository.session]
        assert not await service.close_due(recount=recount, context=ActingContext.worker("test-worker"))

    review = repository.session
    assert review
    assert review.poll
    assert review.status is VoteStatus.REVIEW
    assert review.poll.unreadable_cards == (100,)
    manager = UserActor(account_id=7, discord_user_id=70, guild_id=10, capabilities=frozenset({"vote.poll.close_any"}))
    recount_staff = replace(manager, account_id=8, capabilities=frozenset({"vote.poll.recount"}))
    refused = await service.review_poll(
        review, "finalize", reason="Accept collected ballots", context=ActingContext.for_actor(recount_staff)
    )
    assert refused.rejection is VoteRejection.NOT_AUTHORIZED
    result = await service.review_poll(
        review, "finalize", reason="Card deleted", recount=recount, context=ActingContext.for_actor(manager)
    )
    assert result.just_closed
    assert result.session
    assert result.session.status is VoteStatus.CLOSED
    assert len(set(attempts)) == 3
    stale = await service.review_poll(review, "void", reason="Old preview", context=ActingContext.for_actor(manager))
    assert stale.rejection is VoteRejection.CLOSED


@pytest.mark.parametrize(("action", "status"), [("reopen", VoteStatus.OPEN), ("void", VoteStatus.VOID)])
async def test_manager_can_resolve_a_paused_poll_without_recounting(action: str, status: VoteStatus) -> None:
    snapshot = poll_snapshot(status=VoteStatus.REVIEW)
    repository = FakeVoteRepository(snapshot)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.poll.close_any")))
    manager = UserActor(account_id=7, guild_id=10, capabilities=frozenset({"vote.poll.close_any"}))
    deadline = Instant.now().add(hours=1) if action == "reopen" else None
    result = await service.review_poll(
        snapshot, action, reason="Missing vote card", deadline=deadline, context=ActingContext.for_actor(manager)
    )
    assert result.session is not None
    assert result.session.status is status


async def test_retry_permission_can_read_current_cards_and_seal_a_complete_recount() -> None:
    snapshot = poll_snapshot(status=VoteStatus.REVIEW, messages=(VoteMessage(100, 200, 10),))
    repository = FakeVoteRepository(snapshot)
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("vote.poll.recount")))
    staff = UserActor(account_id=7, guild_id=10, capabilities=frozenset({"vote.poll.recount"}))
    reads = []

    async def recount(session_id: int, revision: int) -> tuple[bool, tuple[int, ...]]:
        reads.append(session_id)
        return False, ()

    result = await service.review_poll(
        snapshot, "retry", reason="", recount=recount, context=ActingContext.for_actor(staff)
    )
    assert reads == [snapshot.id]
    assert result.just_closed


async def test_reviewers_who_can_see_pending_builds_page_review_votes() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(
        uow_factory=lambda: VoteUnitOfWork(repository, grants=vote_grants("build.submission.view_pending"))
    )
    reviewer = ActingContext.direct(UserActor(account_id=7))

    page = await service.list_reviews(ReviewFilters(statuses=(VoteStatus.OPEN,)), context=reviewer)
    edits = await service.list_reviews(ReviewFilters(frozenset({ReviewKind.BUILD_EDIT})), context=reviewer)

    assert ([item.id for item in page.items], page.total) == ([snapshot().id], 1)
    assert edits.items == ()


async def test_review_votes_are_hidden_from_members_who_cannot_see_pending_builds() -> None:
    service = VoteService(uow_factory=lambda: VoteUnitOfWork(FakeVoteRepository(snapshot())))

    with pytest.raises(AuthorizationError):
        await service.list_reviews(ReviewFilters(), context=ActingContext.direct(UserActor(account_id=7)))
