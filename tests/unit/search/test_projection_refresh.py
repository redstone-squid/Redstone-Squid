"""Tests for how one projection run drains the queue."""

from dataclasses import dataclass, field
from functools import partial

from squid.core.actors import ActingContext
from squid.core.search.application.projection import refresh_search_index
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


@dataclass
class FakeProjectionQueue:
    pending: int
    batches: list[int] = field(default_factory=list)

    async def process_batch(self, *, context: ActingContext, limit: int = 50) -> tuple[int, int]:
        taken = min(limit, self.pending)
        self.pending -= taken
        self.batches.append(taken)
        return taken, 0


class ProjectionUnitOfWork(FakeUnitOfWork[FakeProjectionQueue]):
    def __init__(self, queue: FakeProjectionQueue) -> None:
        super().__init__(CommittedState(queue, clone=lambda value: value))

    @property
    def search_projection(self) -> FakeProjectionQueue:
        return self.state


async def test_full_batches_keep_draining_until_one_comes_back_short() -> None:
    queue = FakeProjectionQueue(pending=25)

    result = await refresh_search_index(
        partial(ProjectionUnitOfWork, queue), context=ActingContext.worker("test"), limit=10
    )

    assert result == (25, 0)
    assert queue.batches == [10, 10, 5]


async def test_a_spent_budget_leaves_the_rest_for_the_next_run() -> None:
    queue = FakeProjectionQueue(pending=25)

    result = await refresh_search_index(
        partial(ProjectionUnitOfWork, queue), context=ActingContext.worker("test"), limit=10, budget_seconds=0
    )

    assert result == (10, 0)
    assert queue.pending == 15
