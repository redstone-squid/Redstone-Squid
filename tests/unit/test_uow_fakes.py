"""Shared transaction semantics behind service test doubles."""

from contextlib import nullcontext

import pytest

from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


@pytest.mark.parametrize("raise_error", [False, True])
async def test_leaving_without_commit_preserves_committed_state(raise_error: bool) -> None:
    committed = CommittedState({"values": [1]})
    with pytest.raises(ValueError, match="abandoned") if raise_error else nullcontext():
        async with FakeUnitOfWork(committed) as uow:
            uow.state["values"].append(2)
            if raise_error:
                raise ValueError("abandoned")

    assert committed.value == {"values": [1]}


async def test_commit_captures_state_at_that_moment_for_the_next_scope() -> None:
    committed = CommittedState({"values": [1]})
    async with FakeUnitOfWork(committed) as uow:
        uow.state["values"].append(2)
        await uow.commit()
        uow.state["values"].append(3)

    assert committed.value == {"values": [1, 2]}
    async with FakeUnitOfWork(committed) as next_uow:
        assert next_uow.state == {"values": [1, 2]}
        next_uow.state["values"].clear()
    assert committed.value == {"values": [1, 2]}
