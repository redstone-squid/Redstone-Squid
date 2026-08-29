"""Discord reconciliation application policy tests."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import override

import pytest

from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.sync import (
    DiscordReconciliationService,
    ReconciliationAction,
    ReconciliationJob,
    ReconciliationQueue,
    ReconciliationResource,
)
from squid.sync.infrastructure.models import DiscordSyncQueueItem
from squid.sync.infrastructure.repository import _job as map_row

CLAIM_TOKEN = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _job() -> ReconciliationJob:
    return ReconciliationJob(1, ReconciliationResource.BUILD, "42", ReconciliationAction.REFRESH, 1, 0, CLAIM_TOKEN)


@dataclass(slots=True)
class QueueRecorder(ReconciliationQueue):
    failures: list[tuple[ReconciliationJob, str, int]] = field(default_factory=list)

    @override
    async def claim(self, *, limit: int) -> Sequence[ReconciliationJob]:
        raise AssertionError("invalid claims must be rejected before repository access")

    @override
    async def complete(self, job: ReconciliationJob) -> bool:
        raise AssertionError("completion is outside this policy test")

    @override
    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool:
        self.failures.append((job, error, max_attempts))
        return False


async def test_service_truncates_failure_and_applies_attempt_ceiling() -> None:
    queue = QueueRecorder()
    job = _job()

    assert await DiscordReconciliationService(queue, max_attempts=3).fail(job, RuntimeError("x" * 5000)) is False
    assert queue.failures == [(job, "x" * 4000, 3)]


@pytest.mark.parametrize("limit", [0, 101])
async def test_claim_rejects_unsafe_batch_sizes(limit: int) -> None:
    with pytest.raises(InvalidStateError, match="between 1 and 100"):
        await DiscordReconciliationService(QueueRecorder()).claim(limit)


class TestRowMapping:
    """Persisted spellings become typed jobs or fail at the adapter boundary."""

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

    @pytest.mark.parametrize(("resource_kind", "action"), [("shrubbery", "refresh"), ("build", "incinerate")])
    def test_a_row_outside_its_check_constraint_fails_at_the_boundary(self, resource_kind: str, action: str) -> None:
        with pytest.raises(DataIntegrityError) as raised:
            map_row(self._row(resource_kind=resource_kind, action=action), CLAIM_TOKEN)

        assert raised.value.context["id"] == 1


def test_every_resource_kind_maps_to_a_post_resource() -> None:
    assert {resource.post_kind for resource in ReconciliationResource} == {"build", "vote_session", "starboard_entry"}
