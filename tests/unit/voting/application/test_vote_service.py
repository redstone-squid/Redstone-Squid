from dataclasses import replace

import pytest

from squid.db.schema import VoteKindLiteral, VoteSessionResultLiteral
from squid.services.votes import (
    StoredVoteMutation,
    VoteActor,
    VoteChoice,
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
        target=VoteTarget(build_id=42),
    )


class FakeVoteRepository:
    def __init__(self, session: VoteSessionSnapshot | None):
        self.session = session
        self.cast_calls: list[tuple[int, int, float]] = []
        self.mutation: StoredVoteMutation | None = None

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


@pytest.mark.parametrize(
    ("choice", "is_staff", "expected_weight"),
    [
        (VoteChoice.APPROVE, False, 1.0),
        (VoteChoice.DENY, False, -1.0),
        (VoteChoice.APPROVE, True, 3.0),
        (VoteChoice.DENY, True, -3.0),
    ],
)
async def test_cast_vote_applies_choice_and_staff_weight(
    choice: VoteChoice,
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
        choice,
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, expected_weight)]


async def test_delete_log_vote_requires_trusted_or_staff_actor() -> None:
    repository = FakeVoteRepository(snapshot(kind="delete_log"))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(user_id=7, is_staff=False, is_trusted=False),
        VoteChoice.APPROVE,
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
        VoteChoice.APPROVE,
    )

    assert result.accepted
    assert repository.cast_calls == [(100, 7, 3.0)]


async def test_closed_vote_is_rejected_before_mutation() -> None:
    repository = FakeVoteRepository(snapshot(status="closed", result="approved"))
    service = VoteService(repository)

    result = await service.cast_vote(
        100,
        VoteActor(user_id=7, is_staff=False, is_trusted=True),
        VoteChoice.APPROVE,
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
        VoteChoice.APPROVE,
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
        VoteChoice.DENY,
    )

    assert result.rejection == "closed"
    assert not result.just_closed
