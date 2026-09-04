import logging
from collections.abc import Sequence
from dataclasses import replace
from math import inf, nan

import pytest
from whenever import Instant

from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF
from squid.voting.application import VoteService
from squid.voting.domain import (
    DEFAULT_VOTE_OPTIONS,
    BuildVoteTarget,
    DeleteLogVoteTarget,
    EmojiPreset,
    PollScope,
    RoleWeight,
    StoredVoteMutation,
    VoteActor,
    VoteChange,
    VoteChoice,
    VoteKind,
    VoteMessage,
    VoteOption,
    VoteRejection,
    VoteSelection,
    VoteSessionResult,
    VoteSessionSnapshot,
    VoteStatus,
    VoteVisibility,
)
from squid.voting.errors import InvalidVoteConfigurationError
from tests.support.voting import DEFAULT_BUILD_TARGET, GENERIC_OPTIONS, build_snapshot, poll_snapshot

STAFF = frozenset({VOTE_WEIGHT_STAFF.name})
DELETE_LOG = frozenset({VOTE_LOG_DELETE_CAST.name})
OWNER_GUILD_ID = 10
"""The guild that owns the default snapshot, matching its message location."""

VOTING_GUILD_ID = 999
"""A guild that hosts a card for a shared session without owning it."""


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
    """A build review carded in the owner guild and in a second guild."""
    return replace(
        snapshot(),
        messages=(VoteMessage(100, 200, OWNER_GUILD_ID), VoteMessage(101, 201, VOTING_GUILD_ID)),
    )


class FakeVoteRepository:
    def __init__(self, session: VoteSessionSnapshot | None):
        self.session = session
        self.cast_calls: list[tuple[int, int, int, str, str, float, dict[int, float] | None]] = []
        self.mutation: StoredVoteMutation | None = None
        self.role_weights: dict[tuple[int, VoteKind], list[RoleWeight]] = {}
        self.role_weight_lookups: list[tuple[int, VoteKind]] = []
        self.due: list[VoteSessionSnapshot] = []
        self.generic_scopes: list[PollScope] = []
        self.build_create_calls: list[tuple[int, int, int, int, list[VoteChange], tuple[VoteOption, ...]]] = []
        self.delete_create_calls: list[tuple[int, int, int, int, int, int, tuple[VoteOption, ...]]] = []
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
        options: Sequence[VoteOption],
    ) -> int:
        self.build_create_calls.append(
            (author_account_id, pass_threshold, fail_threshold, build_id, list(changes), tuple(options))
        )
        return 23

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
        self.generic_create_calls.append((author_account_id, question, visibility, deadline, guild_id))
        self.generic_scopes.append(scope)
        return 26

    async def attach_message(self, vote_session_id: int, message_id: int) -> None:
        self.attach_calls.append((vote_session_id, message_id))

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
        self.build_create_calls.append(
            (author_account_id, pass_threshold, fail_threshold, build_id, list(changes), tuple(options))
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
        options: Sequence[VoteOption],
    ) -> int:
        self.delete_create_calls.append(
            (author_account_id, pass_threshold, fail_threshold, message_id, channel_id, server_id, tuple(options))
        )
        return 25

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        return self.session

    async def get_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return self.session

    async def list_open(self, kind: VoteKind) -> Sequence[VoteSessionSnapshot]:
        return [] if self.session is None else [self.session]

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
        self.cast_calls.append((message_id, account_id, guild_id, option_id, emoji, desired_weight, refreshed_weights))
        return self.mutation

    async def close(self, message_id: int) -> StoredVoteMutation | None:
        return self.mutation

    async def close_by_id(self, vote_session_id: int) -> StoredVoteMutation | None:
        return self.mutation

    async def refresh_weights(self, vote_session_id: int, weights: dict[int, float]) -> StoredVoteMutation | None:
        return self.mutation

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]:
        return self.due

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


class MissingActorResolver:
    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> None:
        del account_id, guild_id, kind
        return


class RecordingActorResolver:
    """Resolve members from a fixed roster, recording which guild was asked."""

    def __init__(self, members: dict[tuple[int, int], VoteActor] | None = None):
        self.members = members or {}
        self.calls: list[tuple[int, int]] = []

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        del kind
        self.calls.append((account_id, guild_id))
        if (account_id, guild_id) in self.members:
            return self.members[(account_id, guild_id)]
        # Definitely not a member, as opposed to a guild we could not reach.
        return VoteActor(account_id=account_id, discord_id=account_id * 10, guild_id=guild_id)


async def test_vote_creation_delegates_complete_aggregate_to_repository() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(repository)
    changes: list[VoteChange] = [("status", "pending", "confirmed")]

    build_session_id = await service.start_build_vote(
        author_account_id=7,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=changes,
    )
    delete_session_id = await service.start_delete_log_vote(
        author_account_id=8,
        pass_threshold=4,
        fail_threshold=-2,
        message_id=100,
        channel_id=200,
        server_id=300,
    )

    assert build_session_id == 24
    assert delete_session_id == 25
    assert repository.build_create_calls == [(7, 3, -3, 42, changes, DEFAULT_VOTE_OPTIONS)]
    assert repository.delete_create_calls == [(8, 4, -2, 100, 200, 300, DEFAULT_VOTE_OPTIONS)]


async def test_initial_build_vote_creation_uses_idempotent_repository_operation() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(repository)
    changes: list[VoteChange] = [("submission_status", 0, 1)]

    session_id = await service.ensure_build_submission_vote(
        author_account_id=7,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=changes,
    )

    assert session_id == 23
    assert repository.build_create_calls == [(7, 3, -3, 42, changes, DEFAULT_VOTE_OPTIONS)]


async def test_message_attachment_stays_an_explicit_application_operation() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(repository)

    await service.attach_message(26, 9001)
    await service.attach_message(26, 9001)

    assert repository.attach_calls == [(26, 9001), (26, 9001)]


async def test_refresh_log_carries_session_id_without_user_attributes(caplog: pytest.LogCaptureFixture) -> None:
    initial = replace(
        snapshot(),
        selections=(VoteSelection(account_id=9, guild_id=10, option_id="approve", emoji="👍", weight=1.0),),
    )
    service = VoteService(FakeVoteRepository(initial), actor_resolver=MissingActorResolver())

    with caplog.at_level(logging.WARNING, logger="squid.voting.application.services"):
        await service.refresh(100)

    record_fields = vars(caplog.records[-1])
    assert record_fields["squid.vote.session_id"] == 12
    assert "squid.user.id" not in record_fields


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
    service = VoteService(repository, build_owner_guild_id=OWNER_GUILD_ID)

    result = await service.cast_vote(
        100,
        VoteActor(
            account_id=7,
            discord_id=70,
            guild_id=OWNER_GUILD_ID,
            capabilities=STAFF if has_staff_weight else frozenset(),
        ),
        emoji,
    )

    assert result.accepted
    option_id = "approve" if emoji == "👍" else "deny"
    assert repository.cast_calls == [(100, 7, 10, option_id, emoji, abs(expected_weight), {})]


@pytest.mark.parametrize(
    ("guild_id", "message_id", "emoji"),
    [(OWNER_GUILD_ID, 100, "<:yes:1>"), (VOTING_GUILD_ID, 101, "✅")],
)
async def test_cast_vote_by_session_resolves_a_stable_option_to_each_guild_alias(
    guild_id: int,
    message_id: int,
    emoji: str,
) -> None:
    options = (
        VoteOption("<:yes:1>", VoteChoice.APPROVE, identifier="approve", guild_id=OWNER_GUILD_ID),
        VoteOption("<:no:2>", VoteChoice.DENY, identifier="deny", guild_id=OWNER_GUILD_ID),
        VoteOption("✅", VoteChoice.APPROVE, identifier="approve", guild_id=VOTING_GUILD_ID),
        VoteOption("❌", VoteChoice.DENY, identifier="deny", guild_id=VOTING_GUILD_ID),
    )
    initial = replace(shared_snapshot(), options=options)
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=initial,
        previous_weight=None,
        current_weight=1.0,
        just_closed=False,
    )
    service = VoteService(repository)

    result = await service.cast_vote_by_session(12, VoteActor(7, 70, guild_id=guild_id), "approve")

    assert result.accepted
    assert repository.cast_calls == [(message_id, 7, guild_id, "approve", emoji, 1.0, {})]


async def test_cast_vote_by_session_rejects_missing_session() -> None:
    service = VoteService(FakeVoteRepository(None))

    result = await service.cast_vote_by_session(404, VoteActor(7, 70, guild_id=10), "approve")

    assert result.rejection is VoteRejection.NOT_FOUND
    assert result.session is None


async def test_cast_vote_by_session_rejects_guild_without_message() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(repository)

    result = await service.cast_vote_by_session(12, VoteActor(7, 70, guild_id=999), "approve")

    assert result.rejection is VoteRejection.WRONG_GUILD
    assert repository.cast_calls == []


async def test_cast_vote_by_session_rejects_unknown_option_identifier() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(repository)

    result = await service.cast_vote_by_session(12, VoteActor(7, 70, guild_id=10), "missing")

    assert result.rejection is VoteRejection.INVALID_OPTION
    assert repository.cast_calls == []


async def test_delete_log_vote_requires_the_delete_log_capability() -> None:
    repository = FakeVoteRepository(snapshot(kind=VoteKind.DELETE_LOG))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70),
        "👍",
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
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70, guild_id=OWNER_GUILD_ID, capabilities=STAFF | DELETE_LOG),
        "👍",
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 10, "approve", "👍", 3.0, {})]


async def test_closed_vote_is_rejected_before_mutation() -> None:
    repository = FakeVoteRepository(snapshot(status=VoteStatus.CLOSED, result=VoteSessionResult.APPROVED))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70, capabilities=DELETE_LOG),
        "👍",
    )

    assert result.rejection is VoteRejection.CLOSED
    assert repository.cast_calls == []


async def test_atomic_closure_result_is_exposed_to_adapter_once() -> None:
    initial = snapshot(votes={2: 2.0})
    closed = replace(initial, status=VoteStatus.CLOSED, result=VoteSessionResult.APPROVED, votes={2: 2.0, 7: 1.0})
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=closed,
        previous_weight=None,
        current_weight=1.0,
        just_closed=True,
    )
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70),
        "👍",
    )

    assert result.just_closed
    assert result.session == closed
    assert result.session is not None
    assert result.session.net_votes == 3


async def test_race_with_another_closing_vote_returns_closed_rejection() -> None:
    initial = snapshot()
    repository = FakeVoteRepository(initial)
    repository.mutation = None
    repository.session = initial
    service = VoteService(repository)

    async def get_after_mutation(message_id: int) -> VoteSessionSnapshot:
        if repository.cast_calls:
            return replace(initial, status=VoteStatus.CLOSED, result=VoteSessionResult.DENIED)
        return initial

    repository.get_by_message = get_after_mutation  # type: ignore[method-assign]
    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70),
        "👎",
    )

    assert result.rejection is VoteRejection.CLOSED
    assert not result.just_closed


async def test_custom_vote_option_multiplier_is_applied_before_staff_weight() -> None:
    options = (
        VoteOption("<:strong_yes:123>", VoteChoice.APPROVE, 2.0),
        VoteOption("👎", VoteChoice.DENY),
    )
    initial = replace(snapshot(), options=options)
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=initial,
        previous_weight=None,
        current_weight=6.0,
        just_closed=False,
    )
    service = VoteService(repository, build_owner_guild_id=OWNER_GUILD_ID)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70, guild_id=OWNER_GUILD_ID, capabilities=STAFF),
        "<:strong_yes:123>",
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 10, "approve", "<:strong_yes:123>", 6.0, {})]


async def test_unconfigured_emoji_is_rejected_without_mutation() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70),
        "🤷",
    )

    assert result.rejection is VoteRejection.INVALID_OPTION
    assert repository.cast_calls == []


async def test_vote_options_require_unique_emojis_and_both_choices() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(repository)

    with pytest.raises(InvalidVoteConfigurationError, match="unique"):
        await service.start_build_vote(
            author_account_id=7,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=42,
            changes=[],
            options=(
                VoteOption("👍", VoteChoice.APPROVE),
                VoteOption("👍", VoteChoice.DENY),
            ),
        )

    with pytest.raises(InvalidVoteConfigurationError, match="approve and one deny"):
        await service.start_build_vote(
            author_account_id=7,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=42,
            changes=[],
            options=(VoteOption("👍", VoteChoice.APPROVE),),
        )


@pytest.mark.parametrize("multiplier", [0.0, -1.0, inf, nan])
def test_vote_option_rejects_non_positive_or_non_finite_multiplier(multiplier: float) -> None:
    with pytest.raises(InvalidVoteConfigurationError, match="finite and greater than zero"):
        VoteOption("👍", VoteChoice.APPROVE, multiplier)


async def test_poll_creation_needs_no_guild_and_no_publication_target() -> None:
    """A poll is a database row before it is a Discord message."""
    repository = FakeVoteRepository(None)
    service = VoteService(repository)
    options = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="1", label="One"),
        VoteOption("2️⃣", VoteChoice.GENERIC, identifier="2", label="Two"),
    )

    session_id = await service.create_generic_poll(
        author_account_id=7,
        question="  Which one?  ",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        duration_seconds=3600,
        options=options,
    )

    assert session_id == 26
    author, question, visibility, _deadline, guild_id = repository.generic_create_calls[0]
    assert (author, question, visibility, guild_id) == (7, "Which one?", VoteVisibility.ANONYMOUS_LIVE, None)


@pytest.mark.parametrize("duration_seconds", [59, 30 * 86400 + 1])
async def test_poll_creation_rejects_durations_outside_the_supported_range(duration_seconds: int) -> None:
    service = VoteService(FakeVoteRepository(None))

    with pytest.raises(InvalidVoteConfigurationError, match="between 1 minute and 30 days"):
        await service.create_generic_poll(
            author_account_id=7,
            question="Which one?",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=duration_seconds,
            options=(
                VoteOption("1️⃣", VoteChoice.GENERIC, identifier="1", label="One"),
                VoteOption("2️⃣", VoteChoice.GENERIC, identifier="2", label="Two"),
            ),
        )


async def test_poll_creation_rejects_an_empty_question() -> None:
    service = VoteService(FakeVoteRepository(None))

    with pytest.raises(InvalidVoteConfigurationError, match="question cannot be empty"):
        await service.create_generic_poll(
            author_account_id=7,
            question="   ",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=3600,
            options=(
                VoteOption("1️⃣", VoteChoice.GENERIC, identifier="1", label="One"),
                VoteOption("2️⃣", VoteChoice.GENERIC, identifier="2", label="Two"),
            ),
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
    service = VoteService(repository)

    stranger = await service.close(100, VoteActor(8, 80, guild_id=10))
    creator = await service.close(100, VoteActor(7, 70, guild_id=10))

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
    repository.mutation = StoredVoteMutation(initial, None, 4.0, just_closed=False)
    resolver = RecordingActorResolver(
        {(7, OWNER_GUILD_ID): VoteActor(7, 70, guild_id=OWNER_GUILD_ID, role_ids=frozenset({55}))}
    )
    service = VoteService(repository, actor_resolver=resolver, build_owner_guild_id=OWNER_GUILD_ID)

    result = await service.cast_vote(
        101,
        VoteActor(account_id=7, discord_id=70, guild_id=VOTING_GUILD_ID, role_ids=frozenset({77})),
        "👍",
    )

    assert result.accepted
    assert resolver.calls == [(7, OWNER_GUILD_ID)]
    assert repository.role_weight_lookups == [(OWNER_GUILD_ID, VoteKind.BUILD)]
    assert repository.cast_calls[0][5] == 4.0


async def test_a_voter_outside_the_owner_guild_keeps_the_default_weight() -> None:
    initial = shared_snapshot()
    repository = FakeVoteRepository(initial)
    repository.role_weights = {
        (VOTING_GUILD_ID, VoteKind.BUILD): [RoleWeight(VOTING_GUILD_ID, VoteKind.BUILD, 77, 100.0)]
    }
    repository.mutation = StoredVoteMutation(initial, None, 1.0, just_closed=False)
    service = VoteService(
        repository,
        actor_resolver=RecordingActorResolver(),
        build_owner_guild_id=OWNER_GUILD_ID,
    )

    result = await service.cast_vote(
        101,
        VoteActor(
            account_id=7,
            discord_id=70,
            guild_id=VOTING_GUILD_ID,
            role_ids=frozenset({77}),
            capabilities=STAFF,
        ),
        "👍",
    )

    assert result.accepted
    assert repository.cast_calls[0][5] == 1.0


async def test_an_unreachable_owner_guild_lands_the_ballot_at_the_default_weight() -> None:
    initial = shared_snapshot()
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(initial, None, 1.0, just_closed=False)
    service = VoteService(
        repository,
        actor_resolver=MissingActorResolver(),
        build_owner_guild_id=OWNER_GUILD_ID,
    )

    result = await service.cast_vote(
        101,
        VoteActor(account_id=7, discord_id=70, guild_id=VOTING_GUILD_ID, capabilities=STAFF),
        "👍",
    )

    assert result.accepted
    assert repository.cast_calls[0][5] == 1.0


async def test_without_an_owner_guild_no_role_table_may_weight_a_build_vote() -> None:
    initial = snapshot()
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(initial, None, 1.0, just_closed=False)
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70, guild_id=OWNER_GUILD_ID, capabilities=STAFF),
        "👍",
    )

    assert result.accepted
    assert repository.cast_calls[0][5] == 1.0


async def test_refresh_reweighs_every_ballot_against_the_owner_guild() -> None:
    selections = (
        VoteSelection(account_id=7, guild_id=VOTING_GUILD_ID, option_id="approve", emoji="👍", weight=100.0),
        VoteSelection(account_id=8, guild_id=OWNER_GUILD_ID, option_id="approve", emoji="👍", weight=1.0),
    )
    initial = replace(shared_snapshot(), selections=selections)
    repository = FakeVoteRepository(initial)
    repository.role_weights = {
        (OWNER_GUILD_ID, VoteKind.BUILD): [RoleWeight(OWNER_GUILD_ID, VoteKind.BUILD, 55, 4.0)],
        (VOTING_GUILD_ID, VoteKind.BUILD): [RoleWeight(VOTING_GUILD_ID, VoteKind.BUILD, 77, 100.0)],
    }
    resolver = RecordingActorResolver(
        {(8, OWNER_GUILD_ID): VoteActor(8, 80, guild_id=OWNER_GUILD_ID, role_ids=frozenset({55}))}
    )
    service = VoteService(repository, actor_resolver=resolver, build_owner_guild_id=OWNER_GUILD_ID)

    weights, unresolved = await service._calculate_refresh(initial)

    assert unresolved == []
    # The cast guild is never consulted, so the ballot cast in 999 loses its 100x.
    assert [guild for _, guild in resolver.calls] == [OWNER_GUILD_ID, OWNER_GUILD_ID]
    assert weights == {7: 1.0, 8: 4.0}


async def test_each_kind_names_the_guild_that_owns_its_weights() -> None:
    service = VoteService(FakeVoteRepository(None), build_owner_guild_id=OWNER_GUILD_ID)
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
    assert VoteService(FakeVoteRepository(None))._owner_guild_id(snapshot()) is None


@pytest.mark.parametrize(
    ("session", "target_type", "visibility", "owner_guild_id"),
    [
        (build_snapshot(), BuildVoteTarget, None, OWNER_GUILD_ID),
        (snapshot(kind=VoteKind.DELETE_LOG), DeleteLogVoteTarget, None, OWNER_GUILD_ID),
        *[
            (poll_snapshot(visibility=visibility, guild_id=77), None, visibility, 77)
            for visibility in VoteVisibility
        ],
    ],
    ids=["build", "delete-log", *[f"generic-{visibility.value}" for visibility in VoteVisibility]],
)
def test_kind_visibility_target_matrix_reaches_application_policy(
    session: VoteSessionSnapshot,
    target_type: type[BuildVoteTarget] | type[DeleteLogVoteTarget] | None,
    visibility: VoteVisibility | None,
    owner_guild_id: int,
) -> None:
    """Every valid domain payload keeps its discriminator at the service boundary."""
    service = VoteService(FakeVoteRepository(session), build_owner_guild_id=OWNER_GUILD_ID)

    assert (type(session.target) if session.target is not None else None) is target_type
    assert session.visibility is visibility
    assert service._owner_guild_id(session) == owner_guild_id


async def test_a_weight_edit_reaches_only_the_sessions_that_guild_owns() -> None:
    initial = shared_snapshot()
    repository = FakeVoteRepository(initial)
    resolver = RecordingActorResolver()
    service = VoteService(repository, actor_resolver=resolver, build_owner_guild_id=OWNER_GUILD_ID)

    # A guild that merely hosts a card for the shared review.
    await service.set_role_weight(RoleWeight(VOTING_GUILD_ID, VoteKind.BUILD, 77, 100.0))

    assert resolver.calls == []


async def test_a_weight_edit_in_the_owner_guild_reweighs_its_sessions() -> None:
    selections = (VoteSelection(account_id=7, guild_id=VOTING_GUILD_ID, option_id="approve", emoji="👍", weight=9.0),)
    initial = replace(shared_snapshot(), selections=selections)
    repository = FakeVoteRepository(initial)
    resolver = RecordingActorResolver()
    service = VoteService(repository, actor_resolver=resolver, build_owner_guild_id=OWNER_GUILD_ID)

    await service.set_role_weight(RoleWeight(OWNER_GUILD_ID, VoteKind.BUILD, 55, 4.0))

    assert resolver.calls == [(7, OWNER_GUILD_ID)]


async def test_a_due_poll_without_a_card_still_has_its_weights_recomputed() -> None:
    selections = (VoteSelection(account_id=7, guild_id=77, option_id="one", emoji="1️⃣", weight=9.0),)
    poll = replace(poll_snapshot(guild_id=77), selections=selections, messages=())
    repository = FakeVoteRepository(poll)
    repository.due = [poll]
    resolver = RecordingActorResolver()
    service = VoteService(repository, actor_resolver=resolver)

    await service.close_due()

    assert resolver.calls == [(7, 77)]


async def test_a_network_poll_needs_an_owning_guild_and_unscoped_options() -> None:
    service = VoteService(FakeVoteRepository(None))
    unscoped = (
        VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", label="One"),
        VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", label="Two"),
    )

    with pytest.raises(InvalidVoteConfigurationError, match="must belong to a guild"):
        await service.create_generic_poll(
            author_account_id=7,
            question="Which?",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=3600,
            options=unscoped,
            scope=PollScope.NETWORK,
        )

    with pytest.raises(InvalidVoteConfigurationError, match="not be scoped to one guild"):
        await service.create_generic_poll(
            author_account_id=7,
            question="Which?",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=3600,
            options=GENERIC_OPTIONS,
            guild_id=10,
            scope=PollScope.NETWORK,
        )


async def test_a_network_poll_records_its_scope() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(repository)

    await service.create_generic_poll(
        author_account_id=7,
        question="Which?",
        visibility=VoteVisibility.ANONYMOUS_LIVE,
        duration_seconds=3600,
        options=(
            VoteOption("1️⃣", VoteChoice.GENERIC, identifier="one", label="One"),
            VoteOption("2️⃣", VoteChoice.GENERIC, identifier="two", label="Two"),
        ),
        guild_id=10,
        scope=PollScope.NETWORK,
    )

    assert repository.generic_scopes == [PollScope.NETWORK]
