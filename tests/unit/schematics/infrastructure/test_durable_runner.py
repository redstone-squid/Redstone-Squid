"""How the durable schematic runner reports a job's own failures."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from squid.artifacts.infrastructure import LocalArtifactStore
from squid.config import SchematicConfig
from squid.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobService,
    SchematicJobSnapshot,
)
from squid.schematics.infrastructure.durable import SchematicJobRunner
from tests.unit.schematics.fakes import FakeSchematicAnalyzer


@dataclass(frozen=True, slots=True)
class RecordedFailure:
    error: str
    error_kind: SchematicJobErrorKind
    error_context: Mapping[str, Any]
    terminal: bool


class RecordingJobRepository:
    """Hands out claims once, and records how the runner reported each failure."""

    def __init__(self, *jobs: ClaimedSchematicJob) -> None:
        self.pending = list(jobs)
        self.failures: list[RecordedFailure] = []

    async def submit(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        input_keys: Sequence[str],
    ) -> int:
        raise NotImplementedError

    async def get(self, job_id: int) -> SchematicJobSnapshot | None:
        return None

    async def claim(self, *, limit: int) -> Sequence[ClaimedSchematicJob]:
        claimed, self.pending = self.pending[:limit], self.pending[limit:]
        return claimed

    async def complete(
        self,
        job: ClaimedSchematicJob,
        result: Mapping[str, Any],
        result_object_key: str | None,
        *,
        retention_hours: int,
    ) -> bool:
        return True

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
        self.failures.append(RecordedFailure(error, error_kind, error_context, terminal))
        return terminal

    async def cleanup(self, *, limit: int) -> Sequence[str]:
        return []


async def test_a_missing_input_artifact_is_classified_and_failed_terminally(tmp_path: Path) -> None:
    """The input fan-out must not wrap the error: classification is by exception type."""
    job = ClaimedSchematicJob(
        id=1,
        operation="analyze",
        params={"limits": {}},
        input_keys=("schematics/ab/missing",),
        attempts=0,
        claim_token=uuid.uuid4(),
    )
    repository = RecordingJobRepository(job)
    runner = SchematicJobRunner(
        SchematicJobService(repository),
        LocalArtifactStore(tmp_path / "objects"),
        FakeSchematicAnalyzer(),
        SchematicConfig(),
    )

    await runner.process_batch()

    assert len(repository.failures) == 1
    failure = repository.failures[0]
    # Wrapped, this classified as a retryable unknown with no context at all.
    assert failure.terminal is True
    assert failure.error_kind == "internal"
    assert failure.error_context["object_key"] == "schematics/ab/missing"
