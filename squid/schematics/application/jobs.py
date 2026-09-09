"""Durable schematic job contracts shared by clients and the worker."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr

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
    """A claim-token queue whose rows survive their own acknowledgement so clients can poll them.

    Every transition that takes a `ClaimedSchematicJob` is fenced on its claim token and reports
    `False` instead of acting when the token is no longer current.
    """

    async def submit(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        input_keys: Sequence[str],
    ) -> int:
        """Enqueue one operation over the already-staged `input_keys` and return its job id."""
        ...

    async def get(self, job_id: int) -> SchematicJobSnapshot | None:
        """Current state of one job, or `None` once cleanup has deleted the row."""
        ...

    async def claim(self, *, limit: int) -> Sequence[ClaimedSchematicJob]:
        """Lease up to `limit` unfinished jobs that are due, each with a fresh claim token."""
        ...

    async def complete(
        self,
        job: ClaimedSchematicJob,
        result: Mapping[str, Any],
        result_object_key: str | None,
        *,
        retention_hours: int,
    ) -> bool:
        """Record the result and retain the row for `retention_hours`; `False` when the claim is stale."""
        ...

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
    ) -> bool:
        """Back the job off for a retry, or kill it when `terminal` or attempts reach `max_attempts`.

        Returns whether the job was dead-lettered. `error_kind` and `error_context` are what a
        polling client rebuilds its typed exception from.
        """
        ...

    async def cleanup(self, *, limit: int) -> Sequence[str]:
        """Delete up to `limit` expired jobs, returning the result object keys the caller must delete."""
        ...


class SchematicJobService:
    """Bounds-check durable schematic job calls before they reach the repository.

    Construction raises `InvalidStateError` unless `max_attempts` and `retention_hours` are positive.
    """

    def __init__(
        self,
        repository: SchematicJobRepository,
        *,
        max_attempts: int = 3,
        retention_hours: int = 24,
    ) -> None:
        if max_attempts < 1 or retention_hours < 1:
            msg = tr(t"Schematic job retry and retention settings must be positive.")
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
        """Lease due jobs; raises `InvalidStateError` unless `1 <= limit <= 32`."""
        if not 1 <= limit <= 32:
            msg = tr(t"Schematic job claim limit must be between 1 and 32.")
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
        """Delete expired jobs and return their object keys; `InvalidStateError` unless `1 <= limit <= 500`."""
        if not 1 <= limit <= 500:
            msg = tr(t"Schematic job cleanup limit must be between 1 and 500.")
            raise InvalidStateError(msg)
        return await self._repository.cleanup(limit=limit)
