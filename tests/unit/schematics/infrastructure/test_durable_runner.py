"""How the durable schematic runner reports a job's own failures."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from whenever import Instant

from squid.artifacts.infrastructure import LocalArtifactStore
from squid.config import SchematicConfig
from squid.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicCleanupClaim,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobService,
    SchematicJobSnapshot,
)
from squid.schematics.errors import AmbiguousSimulationInputError, InvalidSchematicError
from squid.schematics.infrastructure.durable import SchematicJobRunner, _job_failure
from tests.support.schematic_job_uow import JobScope
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

    async def renew(self, job: ClaimedSchematicJob) -> bool:
        return True

    async def register_result_object(self, job: ClaimedSchematicJob, object_key: str) -> bool:
        return True

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

    async def claim_cleanup(self, *, limit: int) -> tuple[SchematicCleanupClaim, ...]:
        return ()

    async def complete_cleanup(self, jobs: Sequence[SchematicCleanupClaim]) -> int:
        return 0

    async def release_cleanup(self, jobs: Sequence[SchematicCleanupClaim]) -> None:
        pass


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
        SchematicJobService(uow_factory=partial(JobScope, repository)),
        LocalArtifactStore(tmp_path / "objects"),
        FakeSchematicAnalyzer(),
        SchematicConfig(),
    )

    await runner.process_batch()

    assert len(repository.failures) == 1
    failure = repository.failures[0]
    # ExceptionGroup wrapping would hide the missing-artifact type from failure classification.
    assert failure.terminal is True
    assert failure.error_kind == "internal"
    assert failure.error_context["object_key"] == "schematics/ab/missing"


def test_historical_invalid_jobs_recover_input_choices_without_reclassifying_other_errors() -> None:
    original = AmbiguousSimulationInputError(candidates=((12, 5, -3), (0, 1, 2)))
    snapshot = SchematicJobSnapshot(
        id=42,
        completed_at=None,
        dead_at=Instant.now(),
        result=None,
        result_object_key=None,
        last_error=str(original),
        error_kind="invalid",
        error_context=original.context,
    )

    rebuilt = _job_failure("simulate", 10, snapshot)

    assert isinstance(rebuilt, AmbiguousSimulationInputError)
    assert rebuilt.candidates == original.candidates
    assert rebuilt.rejected is None
    assert rebuilt.public_detail() == original.public_detail()
    assert rebuilt.context["job_id"] == snapshot.id

    invalid = _job_failure(
        "simulate", 10, replace(snapshot, last_error="invalid file", error_context={"reason": "NBT"})
    )
    assert type(invalid) is InvalidSchematicError
    assert invalid.context == {"reason": "NBT", "job_id": snapshot.id}
    assert str(invalid) == "invalid file"
