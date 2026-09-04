"""Durable build-render enrichment coordination."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True)
class ClaimedRenderJob:
    """One claimed request to project a schematic render onto a build."""

    build_id: int
    attempts: int
    claim_token: uuid.UUID
    """The database-minted fence this worker's acknowledgement must still match."""


class SchematicRenderJobRepository(Protocol):
    """Persistence contract for durable build-preview publication work."""

    async def claim(self, *, limit: int) -> Sequence[ClaimedRenderJob]: ...

    async def complete(self, job: ClaimedRenderJob) -> bool: ...

    async def fail(self, job: ClaimedRenderJob, error: str, *, max_attempts: int) -> bool: ...


class SchematicRenderJobService:
    """Claim and acknowledge durable build-preview publication intents."""

    def __init__(self, repository: SchematicRenderJobRepository, *, max_attempts: int = 5) -> None:
        if max_attempts < 1:
            msg = tr(t"Render max_attempts must be positive.")
            raise InvalidStateError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedRenderJob]:
        if not 1 <= limit <= 32:
            msg = tr(t"Render claim limit must be between 1 and 32.")
            raise InvalidStateError(msg)
        return await self._repository.claim(limit=limit)

    async def complete(self, job: ClaimedRenderJob) -> bool:
        return await self._repository.complete(job)

    async def fail(self, job: ClaimedRenderJob, error: Exception) -> bool:
        return await self._repository.fail(job, str(error)[:4000], max_attempts=self._max_attempts)
