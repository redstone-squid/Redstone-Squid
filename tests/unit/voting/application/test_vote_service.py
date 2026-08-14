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
    EmojiPreset,
    RoleWeight,
    StoredVoteMutation,
    VoteActor,
    VoteChange,
    VoteChoice,
    VoteKindLiteral,
    VoteMessage,
    VoteOption,
    VoteSelection,
    VoteSessionResultLiteral,
    VoteSessionSnapshot,
    VoteStatus,
    VoteTarget,
    VoteVisibility,
)
from squid.voting.errors import InvalidVoteConfigurationError

STAFF = frozenset({VOTE_WEIGHT_STAFF.name})
DELETE_LOG = frozenset({VOTE_LOG_DELETE_CAST.name})


def snapshot(
    *,
    kind: VoteKindLiteral = "build",
    status: VoteStatus = "open",
    result: VoteSessionResultLiteral = "pending",
    votes: dict[int, float] | None = None,
) -> VoteSessionSnapshot:
    return VoteSessionSnapshot(
        id=12,
        author_account_id=7,
        kind=kind,
        status=status,
        result=result,
        pass_threshold=3,
        fail_threshold=-3,
        votes=votes or {},
        messages=(VoteMessage(100, 200, 10),),
        options=DEFAULT_VOTE_OPTIONS,
        target=VoteTarget(build_id=42),
    )


class FakeVoteRepository:
    def __init__(self, session: VoteSessionSnapshot | None):
        self.session = session
        self.cast_calls: list[tuple[int, int, int, int, str, str, float, dict[int, float] | None]] = []
        self.mutation: StoredVoteMutation | None = None
        self.build_create_calls: list[tuple[int, int, int, int, list[VoteChange], tuple[VoteOption, ...]]] = []
        self.delete_create_calls: list[tuple[int, int, int, int, int, int, tuple[VoteOption, ...]]] = []

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
        guild_id: int,
        question: str,
        visibility: VoteVisibility,
        deadline: Instant,
        options: Sequence[VoteOption],
    ) -> int:
        return 26

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

    async def list_open(self, kind: VoteKindLiteral) -> Sequence[VoteSessionSnapshot]:
        return [] if self.session is None else [self.session]

    async def cast_vote(
        self,
        message_id: int,
        account_id: int,
        discord_id: int,
        guild_id: int,
        option_id: str,
        emoji: str,
        desired_weight: float,
        refreshed_weights: dict[int, float] | None = None,
    ) -> StoredVoteMutation | None:
        self.cast_calls.append(
            (message_id, account_id, discord_id, guild_id, option_id, emoji, desired_weight, refreshed_weights)
        )
        return self.mutation

    async def close(self, message_id: int) -> StoredVoteMutation | None:
        return self.mutation

    async def close_by_id(self, vote_session_id: int) -> StoredVoteMutation | None:
        return self.mutation

    async def refresh_weights(self, vote_session_id: int, weights: dict[int, float]) -> StoredVoteMutation | None:
        return self.mutation

    async def list_due(self, now: Instant) -> Sequence[VoteSessionSnapshot]:
        return []

    async def get_emoji_preset(self, guild_id: int, kind: VoteKindLiteral) -> EmojiPreset | None:
        return None

    async def set_emoji_preset(self, preset: EmojiPreset) -> None:
        pass

    async def get_role_weights(self, guild_id: int, kind: VoteKindLiteral) -> Sequence[RoleWeight]:
        return []

    async def set_role_weight(self, weight: RoleWeight) -> None:
        pass

    async def remove_role_weight(self, guild_id: int, kind: VoteKindLiteral, role_id: int) -> None:
        pass

    async def reset_configuration(self, guild_id: int, kind: VoteKindLiteral | None = None) -> None:
        pass


class MissingActorResolver:
    async def resolve(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKindLiteral) -> None:
        del account_id, discord_id, guild_id, kind
        return


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


async def test_refresh_log_carries_session_id_without_user_attributes(caplog: pytest.LogCaptureFixture) -> None:
    initial = replace(
        snapshot(),
        selections=(
            VoteSelection(account_id=9, discord_id=90, guild_id=10, option_id="approve", emoji="👍", weight=1.0),
        ),
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
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70, capabilities=STAFF if has_staff_weight else frozenset()),
        emoji,
    )

    assert result.accepted
    option_id = "approve" if emoji == "👍" else "deny"
    assert repository.cast_calls == [(100, 7, 70, 10, option_id, emoji, abs(expected_weight), {})]


async def test_cast_vote_by_session_resolves_guild_option_alias() -> None:
    options = (
        VoteOption("<:yes:1>", VoteChoice.APPROVE, identifier="approve", guild_id=10),
        VoteOption("<:no:2>", VoteChoice.DENY, identifier="deny", guild_id=10),
    )
    initial = replace(snapshot(), options=options)
    repository = FakeVoteRepository(initial)
    repository.mutation = StoredVoteMutation(
        session=initial,
        previous_weight=None,
        current_weight=1.0,
        just_closed=False,
    )
    service = VoteService(repository)

    result = await service.cast_vote_by_session(12, VoteActor(7, 70, guild_id=10), "approve")

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 70, 10, "approve", "<:yes:1>", 1.0, {})]


async def test_cast_vote_by_session_rejects_missing_session() -> None:
    service = VoteService(FakeVoteRepository(None))

    result = await service.cast_vote_by_session(404, VoteActor(7, 70, guild_id=10), "approve")

    assert result.rejection == "not_found"
    assert result.session is None


async def test_cast_vote_by_session_rejects_guild_without_message() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(repository)

    result = await service.cast_vote_by_session(12, VoteActor(7, 70, guild_id=999), "approve")

    assert result.rejection == "wrong_guild"
    assert repository.cast_calls == []


async def test_cast_vote_by_session_rejects_unknown_option_identifier() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(repository)

    result = await service.cast_vote_by_session(12, VoteActor(7, 70, guild_id=10), "missing")

    assert result.rejection == "invalid_option"
    assert repository.cast_calls == []


async def test_delete_log_vote_requires_the_delete_log_capability() -> None:
    repository = FakeVoteRepository(snapshot(kind="delete_log"))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70),
        "👍",
    )

    assert result.rejection == "not_eligible"
    assert repository.cast_calls == []


async def test_the_delete_log_capability_admits_a_voter_and_staff_weight_still_applies() -> None:
    initial = snapshot(kind="delete_log")
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
        VoteActor(account_id=7, discord_id=70, capabilities=STAFF | DELETE_LOG),
        "👍",
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 70, 10, "approve", "👍", 3.0, {})]


async def test_closed_vote_is_rejected_before_mutation() -> None:
    repository = FakeVoteRepository(snapshot(status="closed", result="approved"))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70, capabilities=DELETE_LOG),
        "👍",
    )

    assert result.rejection == "closed"
    assert repository.cast_calls == []


async def test_atomic_closure_result_is_exposed_to_adapter_once() -> None:
    initial = snapshot(votes={2: 2.0})
    closed = replace(initial, status="closed", result="approved", votes={2: 2.0, 7: 1.0})
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
            return replace(initial, status="closed", result="denied")
        return initial

    repository.get_by_message = get_after_mutation  # type: ignore[method-assign]
    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70),
        "👎",
    )

    assert result.rejection == "closed"
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
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70, capabilities=STAFF),
        "<:strong_yes:123>",
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 70, 10, "approve", "<:strong_yes:123>", 6.0, {})]


async def test_unconfigured_emoji_is_rejected_without_mutation() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(account_id=7, discord_id=70),
        "🤷",
    )

    assert result.rejection == "invalid_option"
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
