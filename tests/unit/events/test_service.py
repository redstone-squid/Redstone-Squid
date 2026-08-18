"""Domain-event application service tests."""

from unittest.mock import AsyncMock

import pytest
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.events import DomainEvent, DomainEventDelivery, DomainEventService


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


async def test_service_delegates_claim_and_acknowledgement() -> None:
    repository = AsyncMock()
    repository.claim.return_value = (_delivery(),)
    repository.complete.return_value = True
    service = DomainEventService(repository)

    assert await service.claim("discord", 10) == (_delivery(),)
    assert await service.complete(_delivery()) is True
    repository.claim.assert_awaited_once_with(consumer="discord", limit=10)
    repository.complete.assert_awaited_once_with(_delivery())


async def test_service_truncates_failure_and_applies_attempt_ceiling() -> None:
    repository = AsyncMock()
    repository.fail.return_value = False
    service = DomainEventService(repository, max_attempts=3)

    assert await service.fail(_delivery(), RuntimeError("x" * 5000)) is False
    args = repository.fail.await_args
    assert len(args.args[1]) == 4000
    assert args.kwargs == {"max_attempts": 3}


async def test_service_permanently_rejects_invalid_contracts() -> None:
    repository = AsyncMock()
    repository.reject.return_value = True
    service = DomainEventService(repository)

    assert await service.reject(_delivery(), ValueError("unsupported")) is True
    repository.reject.assert_awaited_once_with(_delivery(), "unsupported")


@pytest.mark.parametrize("limit", [0, 101])
async def test_claim_rejects_unsafe_batch_sizes(limit: int) -> None:
    with pytest.raises(InvalidStateError, match="between 1 and 100"):
        await DomainEventService(AsyncMock()).claim("discord", limit)


async def test_claim_rejects_an_empty_consumer() -> None:
    with pytest.raises(InvalidStateError, match="non-empty name"):
        await DomainEventService(AsyncMock()).claim("")


def test_service_rejects_a_non_positive_attempt_ceiling() -> None:
    with pytest.raises(InvalidStateError, match="must be positive"):
        DomainEventService(AsyncMock(), max_attempts=0)
