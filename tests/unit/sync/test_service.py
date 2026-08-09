"""Discord reconciliation application service tests."""

from unittest.mock import AsyncMock

import pytest
from whenever import Instant

from squid.sync import DiscordSyncService, SyncJob


def _job() -> SyncJob:
    return SyncJob(1, "build", "42", "refresh", 1, 0, Instant.from_utc(2026, 8, 5))


async def test_service_delegates_claim_and_acknowledgement() -> None:
    repository = AsyncMock()
    repository.claim.return_value = (_job(),)
    repository.complete.return_value = True
    service = DiscordSyncService(repository)

    assert await service.claim(10) == (_job(),)
    assert await service.complete(_job()) is True
    repository.claim.assert_awaited_once_with(limit=10)
    repository.complete.assert_awaited_once_with(_job())


async def test_service_truncates_failure_and_applies_attempt_ceiling() -> None:
    repository = AsyncMock()
    repository.fail.return_value = False
    service = DiscordSyncService(repository, max_attempts=3)

    assert await service.fail(_job(), RuntimeError("x" * 5000)) is False
    args = repository.fail.await_args
    assert len(args.args[1]) == 4000
    assert args.kwargs == {"max_attempts": 3}


@pytest.mark.parametrize("limit", [0, 101])
async def test_claim_rejects_unsafe_batch_sizes(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        await DiscordSyncService(AsyncMock()).claim(limit)
