"""Durable schematic job contracts shared by clients and the worker."""

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import anyio
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory

type SchematicJobOperation = Literal[
    "capabilities",
    "analyze",
    "convert",
    "compare",
    "render",
    "simulate",
    "autostack",
]
type SchematicJobErrorKind = Literal[
    "invalid", "ambiguous_simulation_input", "too_large", "unavailable", "timeout", "crashed", "internal"
]


@dataclass(frozen=True, slots=True)
class ClaimedSchematicJob:
    """One durable native-engine request leased by a worker."""

    id: int
    operation: SchematicJobOperation
    params: Mapping[str, Any]
    input_keys: tuple[str, ...]
    attempts: int
    claim_token: uuid.UUID
    """The database-minted fence this worker's acknowledgement must still match."""


@dataclass(frozen=True, slots=True)
class SchematicJobSnapshot:
    """Client-visible state of one submitted job."""

    id: int
    completed_at: Instant | None
    dead_at: Instant | None
    result: Mapping[str, Any] | None
    result_object_key: str | None
    last_error: str | None
    error_kind: SchematicJobErrorKind | None
    error_context: Mapping[str, Any]


class SchematicJobRepository(Protocol):
    """Persistence operations for durable schematic work."""

    async def submit(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        input_keys: Sequence[str],
    ) -> int: ...

    async def get(self, job_id: int) -> SchematicJobSnapshot | None: ...

    async def claim(self, *, limit: int) -> Sequence[ClaimedSchematicJob]: ...

    async def register_result_object(self, job: ClaimedSchematicJob, object_key: str) -> bool:
        """Retain an output for cleanup only while this claim owns the job."""
        ...

    async def complete(
        self,
        job: ClaimedSchematicJob,
        result: Mapping[str, Any],
        result_object_key: str | None,
        *,
        retention_hours: int,
    ) -> bool: ...

    async def fail(
        self,
        job: ClaimedSchematicJob,
        error: str,
        *,
        error_kind: SchematicJobErrorKind,
        error_context: Mapping[str, Any],
        max_attempts: int,
        terminal: bool,
        retention_hours: int,
    ) -> bool: ...

    async def claim_cleanup(self, *, limit: int) -> tuple[SchematicCleanupClaim, ...]: ...
    async def complete_cleanup(self, jobs: Sequence[SchematicCleanupClaim]) -> int: ...
    async def release_cleanup(self, jobs: Sequence[SchematicCleanupClaim]) -> None: ...


class SchematicJobService:
    """Validate and coordinate durable schematic jobs."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[SchematicJobUnitOfWork],
        max_attempts: int = 3,
        retention_hours: int = 24,
    ) -> None:
        if max_attempts < 1 or retention_hours < 1:
            msg = tr(t"Schematic job retry and retention settings must be positive.")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._max_attempts = max_attempts
        self._retention_hours = retention_hours

    async def submit(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        input_keys: Sequence[str],
    ) -> int:
        async with self._uow_factory() as uow:
            result = await uow.schematic_jobs.submit(operation, params, input_keys)
            await uow.commit()
            return result

    async def get(self, job_id: int) -> SchematicJobSnapshot | None:
        async with self._uow_factory() as uow:
            return await uow.schematic_jobs.get(job_id)

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedSchematicJob]:
        async with self._uow_factory() as uow:
            if not 1 <= limit <= 32:
                msg = tr(t"Schematic job claim limit must be between 1 and 32.")
                raise InvalidStateError(msg)
            result = await uow.schematic_jobs.claim(limit=limit)
            await uow.commit()
            return result

    async def register_result_object(self, job: ClaimedSchematicJob, object_key: str) -> bool:
        """Record cleanup ownership before a worker uploads an output object."""
        async with self._uow_factory() as uow:
            result = await uow.schematic_jobs.register_result_object(job, object_key)
            await uow.commit()
            return result

    async def complete(
        self,
        job: ClaimedSchematicJob,
        result: Mapping[str, Any],
        result_object_key: str | None,
    ) -> bool:
        async with self._uow_factory() as uow:
            applied = await uow.schematic_jobs.complete(
                job,
                result,
                result_object_key,
                retention_hours=self._retention_hours,
            )
            await uow.commit()
            return applied

    async def fail(
        self,
        job: ClaimedSchematicJob,
        error: Exception,
        *,
        error_kind: SchematicJobErrorKind,
        error_context: Mapping[str, Any],
        terminal: bool,
    ) -> bool:
        async with self._uow_factory() as uow:
            result = await uow.schematic_jobs.fail(
                job,
                str(error),
                error_kind=error_kind,
                error_context=error_context,
                max_attempts=self._max_attempts,
                terminal=terminal,
                retention_hours=self._retention_hours,
            )
            await uow.commit()
            return result

    async def cleanup(self, delete_object: Callable[[str], Awaitable[None]], *, limit: int = 100) -> int:
        if not 1 <= limit <= 500:
            msg = tr(t"Schematic job cleanup limit must be between 1 and 500.")
            raise InvalidStateError(msg)
        async with self._uow_factory() as uow:
            jobs = await uow.schematic_jobs.claim_cleanup(limit=limit)
            await uow.commit()
        try:
            with anyio.fail_after(240):
                for key in dict.fromkeys(key for job in jobs for key in job.object_keys):
                    await delete_object(key)
        except Exception:
            async with self._uow_factory() as uow:
                await uow.schematic_jobs.release_cleanup(jobs)
                await uow.commit()
            raise
        async with self._uow_factory() as uow:
            removed = await uow.schematic_jobs.complete_cleanup(jobs)
            await uow.commit()
            return removed


@dataclass(frozen=True, slots=True)
class SchematicCleanupClaim:
    """An expired job whose retained outputs are reserved for deletion."""

    id: int
    claim_token: uuid.UUID
    object_keys: tuple[str, ...]


class SchematicJobUnitOfWork(UnitOfWork, Protocol):
    @property
    def schematic_jobs(self) -> SchematicJobRepository: ...
