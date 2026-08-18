"""Discord reconciliation application service tests."""

import uuid
from unittest.mock import AsyncMock

import pytest

from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.sync import (
    DiscordReconciliationService,
    ReconciliationAction,
    ReconciliationJob,
    ReconciliationResource,
)
from squid.sync.infrastructure.models import DiscordSyncQueueItem
from squid.sync.infrastructure.repository import _job as map_row

CLAIM_TOKEN = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _job() -> ReconciliationJob:
    return ReconciliationJob(
        1,
        ReconciliationResource.BUILD,
        "42",
        ReconciliationAction.REFRESH,
        1,
        0,
        CLAIM_TOKEN,
    )


async def test_service_delegates_claim_and_acknowledgement() -> None:
    repository = AsyncMock()
    repository.claim.return_value = (_job(),)
    repository.complete.return_value = True
    service = DiscordReconciliationService(repository)

    assert await service.claim(10) == (_job(),)
    assert await service.complete(_job()) is True
    repository.claim.assert_awaited_once_with(limit=10)
    repository.complete.assert_awaited_once_with(_job())


async def test_service_truncates_failure_and_applies_attempt_ceiling() -> None:
    repository = AsyncMock()
    repository.fail.return_value = False
    service = DiscordReconciliationService(repository, max_attempts=3)

    assert await service.fail(_job(), RuntimeError("x" * 5000)) is False
    args = repository.fail.await_args
    assert len(args.args[1]) == 4000
    assert args.kwargs == {"max_attempts": 3}


@pytest.mark.parametrize("limit", [0, 101])
async def test_claim_rejects_unsafe_batch_sizes(limit: int) -> None:
    with pytest.raises(InvalidStateError, match="between 1 and 100"):
        await DiscordReconciliationService(AsyncMock()).claim(limit)


class TestRowMapping:
    """Two text columns used to be cast into their types at the boundary."""

    @staticmethod
    def _row(*, resource_kind: str = "build", action: str = "refresh") -> DiscordSyncQueueItem:
        row = DiscordSyncQueueItem(resource_kind=resource_kind, source_key="42", action=action, generation=9)
        row.id = 1
        row.attempts = 2
        return row

    def test_a_valid_row_becomes_a_typed_job(self) -> None:
        job = map_row(self._row(action="delete"), CLAIM_TOKEN)

        assert job.resource_kind is ReconciliationResource.BUILD
        assert job.action is ReconciliationAction.DELETE
        assert job.generation == 9
        assert job.claim_token == CLAIM_TOKEN

    @pytest.mark.parametrize(
        ("resource_kind", "action"),
        [("shrubbery", "refresh"), ("build", "incinerate")],
    )
    def test_a_row_outside_its_check_constraint_fails_at_the_boundary(self, resource_kind: str, action: str) -> None:
        """A cast let such a row reach the reconciler looking valid and fail
        somewhere else entirely."""
        with pytest.raises(DataIntegrityError) as raised:
            map_row(self._row(resource_kind=resource_kind, action=action), CLAIM_TOKEN)

        assert raised.value.context["id"] == 1


def test_every_resource_kind_has_a_post_spelling() -> None:
    """Adding a resource has to fail here rather than at the renderer lookup."""
    assert {resource.post_kind for resource in ReconciliationResource} == {
        "build",
        "vote_session",
        "starboard_entry",
    }
