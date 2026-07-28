from collections.abc import Sequence
from dataclasses import replace
from math import inf, nan

import pytest

from squid.db.schema import VoteKindLiteral, VoteSessionResultLiteral
from squid.services.votes import (
    DEFAULT_VOTE_OPTIONS,
    StoredVoteMutation,
    VoteActor,
    VoteChange,
    VoteChoice,
    VoteOption,
    VoteService,
    VoteSessionSnapshot,
    VoteStatus,
    VoteTarget,
)


def snapshot(
    *,
    kind: VoteKindLiteral = "build",
    status: VoteStatus = "open",
    result: VoteSessionResultLiteral = "pending",
    votes: dict[int, float] | None = None,
) -> VoteSessionSnapshot:
    return VoteSessionSnapshot(
        id=12,
        kind=kind,
        status=status,
        result=result,
        pass_threshold=3,
        fail_threshold=-3,
        votes=votes or {},
        message_ids=(100,),
        options=DEFAULT_VOTE_OPTIONS,
        target=VoteTarget(build_id=42),
    )


class FakeVoteRepository:
    def __init__(self, session: VoteSessionSnapshot | None):
        self.session = session
        self.cast_calls: list[tuple[int, int, float]] = []
        self.mutation: StoredVoteMutation | None = None
        self.build_create_calls: list[tuple[int, int, int, int, list[VoteChange], tuple[VoteOption, ...]]] = []
        self.delete_create_calls: list[tuple[int, int, int, int, int, int, tuple[VoteOption, ...]]] = []

    async def create_build_session(
        self,
        *,
        author_id: int,
        pass_threshold: int,
        fail_threshold: int,
        build_id: int,
        changes: Sequence[VoteChange],
        options: Sequence[VoteOption],
    ) -> int:
        self.build_create_calls.append(
            (author_id, pass_threshold, fail_threshold, build_id, list(changes), tuple(options))
        )
        return 24

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
    ) -> int:
        self.delete_create_calls.append(
            (author_id, pass_threshold, fail_threshold, message_id, channel_id, server_id, tuple(options))
        )
        return 25

    async def get_by_message(self, message_id: int) -> VoteSessionSnapshot | None:
        return self.session

    async def cast_vote(
        self,
        message_id: int,
        user_id: int,
        desired_weight: float,
    ) -> StoredVoteMutation | None:
        self.cast_calls.append((message_id, user_id, desired_weight))
        return self.mutation


async def test_vote_creation_delegates_complete_aggregate_to_repository() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(repository)
    changes: list[VoteChange] = [("status", "pending", "confirmed")]

    build_session_id = await service.start_build_vote(
        author_id=7,
        pass_threshold=3,
        fail_threshold=-3,
        build_id=42,
        changes=changes,
    )
    delete_session_id = await service.start_delete_log_vote(
        author_id=8,
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


@pytest.mark.parametrize(
    ("emoji", "is_staff", "expected_weight"),
    [
        ("👍", False, 1.0),
        ("👎", False, -1.0),
        ("👍", True, 3.0),
        ("👎", True, -3.0),
    ],
)
async def test_cast_vote_applies_choice_and_staff_weight(
    emoji: str,
    is_staff: bool,
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
        VoteActor(user_id=7, is_staff=is_staff, is_trusted=False),
        emoji,
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, expected_weight)]


async def test_delete_log_vote_requires_trusted_or_staff_actor() -> None:
    repository = FakeVoteRepository(snapshot(kind="delete_log"))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(user_id=7, is_staff=False, is_trusted=False),
        "👍",
    )

    assert result.rejection == "not_eligible"
    assert repository.cast_calls == []


async def test_staff_actor_can_vote_on_delete_log_without_trusted_flag() -> None:
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
        VoteActor(user_id=7, is_staff=True, is_trusted=False),
        "👍",
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 3.0)]


async def test_closed_vote_is_rejected_before_mutation() -> None:
    repository = FakeVoteRepository(snapshot(status="closed", result="approved"))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(user_id=7, is_staff=False, is_trusted=True),
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
        VoteActor(user_id=7, is_staff=False, is_trusted=False),
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
        VoteActor(user_id=7, is_staff=False, is_trusted=False),
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
        VoteActor(user_id=7, is_staff=True, is_trusted=False),
        "<:strong_yes:123>",
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 6.0)]


async def test_unconfigured_emoji_is_rejected_without_mutation() -> None:
    repository = FakeVoteRepository(snapshot())
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(user_id=7, is_staff=False, is_trusted=False),
        "🤷",
    )

    assert result.rejection == "invalid_option"
    assert repository.cast_calls == []


async def test_vote_options_require_unique_emojis_and_both_choices() -> None:
    repository = FakeVoteRepository(None)
    service = VoteService(repository)

    with pytest.raises(ValueError, match="unique"):
        await service.start_build_vote(
            author_id=7,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=42,
            changes=[],
            options=(
                VoteOption("👍", VoteChoice.APPROVE),
                VoteOption("👍", VoteChoice.DENY),
            ),
        )

    with pytest.raises(ValueError, match="approve and one deny"):
        await service.start_build_vote(
            author_id=7,
            pass_threshold=3,
            fail_threshold=-3,
            build_id=42,
            changes=[],
            options=(VoteOption("👍", VoteChoice.APPROVE),),
        )


@pytest.mark.parametrize("multiplier", [0.0, -1.0, inf, nan])
def test_vote_option_rejects_non_positive_or_non_finite_multiplier(multiplier: float) -> None:
    with pytest.raises(ValueError, match="finite and greater than zero"):
        VoteOption("👍", VoteChoice.APPROVE, multiplier)
