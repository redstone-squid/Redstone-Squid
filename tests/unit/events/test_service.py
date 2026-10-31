"""Domain-event application policy tests."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import override

import pytest
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.events import DomainEvent, DomainEventDelivery, DomainEventRepository, DomainEventService


def _delivery() -> DomainEventDelivery:
    event = DomainEvent(
        id=7,
        event_type="build.confirmed",
        aggregate_kind="build",
        aggregate_id=42,
        occurred_at=Instant.from_utc(2026, 8, 9),
        payload={"status": 1},
    )
    return DomainEventDelivery(event=event, consumer="discord", attempts=0, claimed_at=Instant.from_utc(2026, 8, 9))


@dataclass(slots=True)
class EventRepositoryRecorder(DomainEventRepository):
    failures: list[tuple[DomainEventDelivery, str, int]] = field(default_factory=list)
    rejections: list[tuple[DomainEventDelivery, str]] = field(default_factory=list)

    @override
    async def claim(self, *, consumer: str, limit: int) -> Sequence[DomainEventDelivery]:
        raise AssertionError("invalid claims must be rejected before repository access")

    @override
    async def complete(self, delivery: DomainEventDelivery) -> bool:
        raise AssertionError("completion is outside this policy test")

    @override
    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool:
        self.failures.append((delivery, error, max_attempts))
        return False

    @override
    async def reject(self, delivery: DomainEventDelivery, error: str) -> bool:
        self.rejections.append((delivery, error))
        return True


async def test_service_truncates_failure_and_applies_attempt_ceiling() -> None:
    repository = EventRepositoryRecorder()
    delivery = _delivery()

    assert await DomainEventService(repository, max_attempts=3).fail(delivery, RuntimeError("x" * 5000)) is False
    assert repository.failures == [(delivery, "x" * 4000, 3)]


async def test_service_truncates_permanent_rejection_details() -> None:
    repository = EventRepositoryRecorder()
    delivery = _delivery()

    assert await DomainEventService(repository).reject(delivery, ValueError("x" * 5000)) is True
    assert repository.rejections == [(delivery, "x" * 4000)]


@pytest.mark.parametrize("limit", [0, 101])
async def test_claim_rejects_unsafe_batch_sizes(limit: int) -> None:
    with pytest.raises(InvalidStateError, match="between 1 and 100"):
        await DomainEventService(EventRepositoryRecorder()).claim("discord", limit)


async def test_claim_rejects_an_empty_consumer() -> None:
    with pytest.raises(InvalidStateError, match="non-empty name"):
        await DomainEventService(EventRepositoryRecorder()).claim("")


def test_service_rejects_a_non_positive_attempt_ceiling() -> None:
    with pytest.raises(InvalidStateError, match="must be positive"):
        DomainEventService(EventRepositoryRecorder(), max_attempts=0)
