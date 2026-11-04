"""Queue requests to update a build's generated preview.

Each preview job identifies a build whose current primary attachment should be rendered and
published. SchematicPreviewWorker handles that workflow. SchematicJobService manages a separate
queue for individual native operations, including the render operation that produces PNG bytes.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class ClaimedRenderJob:
    """One claimed request to publish a generated schematic preview for a build."""

    build_id: int
    attempts: int
    claim_token: uuid.UUID
    """The token that must still match when this worker records an outcome."""


class SchematicRenderJobRepository(Protocol):
    """Persistence contract for durable build-preview publication work."""

    async def claim(self, *, limit: int) -> Sequence[ClaimedRenderJob]: ...

    async def complete(self, job: ClaimedRenderJob) -> bool: ...

    async def fail(self, job: ClaimedRenderJob, error: str, *, max_attempts: int) -> bool: ...


class SchematicRenderJobService:
    """Claim preview jobs for SchematicPreviewWorker and record their outcomes.

    Each call commits a separate transaction so rendering and uploading do not hold database locks.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory[SchematicRenderUnitOfWork], max_attempts: int = 5) -> None:
        if max_attempts < 1:
            msg = tr(t"Render max_attempts must be positive.")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._max_attempts = max_attempts

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedRenderJob]:
        """Commit and return up to limit leases for preview publication."""
        async with self._uow_factory() as uow:
            if not 1 <= limit <= 32:
                msg = tr(t"Render claim limit must be between 1 and 32.")
                raise InvalidStateError(msg)
            result = await uow.schematic_renders.claim(limit=limit)
            await uow.commit()
            return result

    async def complete(self, job: ClaimedRenderJob) -> bool:
        """Remove the job if this claim still owns it, returning whether the removal applied."""
        async with self._uow_factory() as uow:
            result = await uow.schematic_renders.complete(job)
            await uow.commit()
            return result

    async def fail(self, job: ClaimedRenderJob, error: Exception) -> bool:
        """Record a failure and return whether this call exhausted the job's retries.

        False also covers a stale claim whose update was ignored.
        """
        async with self._uow_factory() as uow:
            result = await uow.schematic_renders.fail(job, str(error)[:4000], max_attempts=self._max_attempts)
            await uow.commit()
            return result


class SchematicRenderUnitOfWork(UnitOfWork, Protocol):
    @property
    def schematic_renders(self) -> SchematicRenderJobRepository: ...
