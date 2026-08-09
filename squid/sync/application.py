"""Application service for durable Discord reconciliation work."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from whenever import Instant

type ResourceKind = Literal["build", "vote_session"]
type SyncAction = Literal["refresh", "delete"]


@dataclass(frozen=True, slots=True)
class SyncJob:
    """One claimed reconciliation request."""

    id: int
    resource_kind: ResourceKind
    source_key: str
    action: SyncAction
    generation: int
    attempts: int
    claimed_at: Instant


class SyncQueueRepository(Protocol):
    """Persistence required by the Discord reconciliation drainer."""

    async def claim(self, *, limit: int) -> Sequence[SyncJob]: ...

    async def complete(self, job: SyncJob) -> bool: ...

    async def fail(self, job: SyncJob, error: str, *, max_attempts: int) -> bool: ...


class DiscordSyncService:
    """Claim and acknowledge durable Discord refresh requests."""

    def __init__(self, repository: SyncQueueRepository, *, max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be positive"
            raise ValueError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, limit: int = 20) -> Sequence[SyncJob]:
        """Claim ready work, reclaiming jobs abandoned by crashed workers."""
        if not 1 <= limit <= 100:
            msg = "claim limit must be between 1 and 100"
            raise ValueError(msg)
        return await self._repository.claim(limit=limit)

    async def complete(self, job: SyncJob) -> bool:
        """Acknowledge a job only if its claim is still current."""
        return await self._repository.complete(job)

    async def fail(self, job: SyncJob, error: Exception) -> bool:
        """Retry failed work with backoff, or dead-letter it at the attempt ceiling.

        Returns whether the job was dead-lettered.
        """
        return await self._repository.fail(job, str(error)[:4000], max_attempts=self._max_attempts)
