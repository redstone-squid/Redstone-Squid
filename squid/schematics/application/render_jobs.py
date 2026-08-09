"""Durable build-render enrichment coordination."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from whenever import Instant


@dataclass(frozen=True, slots=True)
class ClaimedRenderJob:
    """One claimed request to project a schematic render onto a build."""

    build_id: int
    attempts: int
    claimed_at: Instant


class SchematicRenderJobRepository(Protocol):
    """Persistence contract for build-render projection work."""

    async def claim(self, *, limit: int) -> Sequence[ClaimedRenderJob]: ...

    async def complete(self, job: ClaimedRenderJob) -> bool: ...

    async def fail(self, job: ClaimedRenderJob, error: str, *, max_attempts: int) -> bool: ...


class SchematicRenderJobService:
    """Claim and acknowledge durable build-render projections."""

    def __init__(self, repository: SchematicRenderJobRepository, *, max_attempts: int = 5) -> None:
        if max_attempts < 1:
            msg = "Render max_attempts must be positive."
            raise ValueError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedRenderJob]:
        if not 1 <= limit <= 32:
            msg = "Render claim limit must be between 1 and 32."
            raise ValueError(msg)
        return await self._repository.claim(limit=limit)

    async def complete(self, job: ClaimedRenderJob) -> bool:
        return await self._repository.complete(job)

    async def fail(self, job: ClaimedRenderJob, error: Exception) -> bool:
        return await self._repository.fail(job, str(error)[:4000], max_attempts=self._max_attempts)
