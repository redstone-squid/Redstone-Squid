"""Durable schematic job contracts shared by clients and the worker."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.core.i18n import _

type SchematicJobOperation = Literal[
    "capabilities",
    "analyze",
    "convert",
    "compare",
    "render",
    "simulate",
    "autostack",
]
type SchematicJobErrorKind = Literal["invalid", "too_large", "unavailable", "timeout", "crashed", "internal"]


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

    async def cleanup(self, *, limit: int) -> Sequence[str]: ...


class SchematicJobService:
    """Validate and coordinate durable schematic jobs."""

    def __init__(
        self,
        repository: SchematicJobRepository,
        *,
        max_attempts: int = 3,
        retention_hours: int = 24,
    ) -> None:
        if max_attempts < 1 or retention_hours < 1:
            msg = _("Schematic job retry and retention settings must be positive.")
            raise InvalidStateError(msg)
        self._repository = repository
        self._max_attempts = max_attempts
        self._retention_hours = retention_hours

    async def submit(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        input_keys: Sequence[str],
    ) -> int:
        return await self._repository.submit(operation, params, input_keys)

    async def get(self, job_id: int) -> SchematicJobSnapshot | None:
        return await self._repository.get(job_id)

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedSchematicJob]:
        if not 1 <= limit <= 32:
            msg = _("Schematic job claim limit must be between 1 and 32.")
            raise InvalidStateError(msg)
        return await self._repository.claim(limit=limit)

    async def complete(
        self,
        job: ClaimedSchematicJob,
        result: Mapping[str, Any],
        result_object_key: str | None,
    ) -> bool:
        return await self._repository.complete(
            job,
            result,
            result_object_key,
            retention_hours=self._retention_hours,
        )

    async def fail(
        self,
        job: ClaimedSchematicJob,
        error: Exception,
        *,
        error_kind: SchematicJobErrorKind,
        error_context: Mapping[str, Any],
        terminal: bool,
    ) -> bool:
        return await self._repository.fail(
            job,
            str(error),
            error_kind=error_kind,
            error_context=error_context,
            max_attempts=self._max_attempts,
            terminal=terminal,
            retention_hours=self._retention_hours,
        )

    async def cleanup(self, *, limit: int = 100) -> Sequence[str]:
        if not 1 <= limit <= 500:
            msg = _("Schematic job cleanup limit must be between 1 and 500.")
            raise InvalidStateError(msg)
        return await self._repository.cleanup(limit=limit)
