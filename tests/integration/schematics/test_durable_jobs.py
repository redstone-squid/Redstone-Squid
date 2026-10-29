"""Real-PostgreSQL coverage for durable schematic execution."""

from collections.abc import AsyncGenerator, Coroutine, Mapping
from pathlib import Path
from typing import Any, Literal, override

import anyio
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.artifacts.application import ArtifactMetadata
from squid.artifacts.infrastructure import LocalArtifactStore
from squid.config import SchematicConfig
from squid.persistence.base import Base
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.application.jobs import ClaimedSchematicJob, SchematicJobService
from squid.schematics.domain.models import AutostackLattice, FingerprintPreset, SchematicFormat, SchematicLimits
from squid.schematics.domain.values import VerifiedResourcePack
from squid.schematics.errors import AmbiguousSimulationInputError, InvalidSchematicError
from squid.schematics.infrastructure import wire
from squid.schematics.infrastructure.durable import QueuedSchematicAnalyzer, SchematicJobRunner
from squid.schematics.infrastructure.jobs import PostgresSchematicJobRepository
from squid.schematics.infrastructure.models import SchematicJob
from tests.unit.schematics.fakes import FakeSchematicAnalyzer

pytestmark = pytest.mark.asyncio

_TABLE = Base.metadata.tables["schematic_jobs"]


@pytest.fixture
async def schematic_job_table(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[_TABLE])
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=[_TABLE])


@pytest.fixture
def durable_components(
    schematic_job_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> tuple[QueuedSchematicAnalyzer, SchematicJobRunner, FakeSchematicAnalyzer, SchematicJobService]:
    del schematic_job_table
    config = SchematicConfig(job_poll_interval_seconds=0.005, job_wait_timeout_seconds=3)
    artifacts = LocalArtifactStore(tmp_path / "objects")
    jobs = SchematicJobService(PostgresSchematicJobRepository(async_session_factory))
    native = FakeSchematicAnalyzer()
    return (
        QueuedSchematicAnalyzer(jobs, artifacts, config),
        SchematicJobRunner(jobs, artifacts, native, config),
        native,
        jobs,
    )


async def _with_runner[ResultT](runner: SchematicJobRunner, request: Coroutine[Any, Any, ResultT]) -> ResultT:
    finished = anyio.Event()
    results: list[ResultT] = []
    failures: list[Exception] = []

    async def execute() -> None:
        try:
            results.append(await request)
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(execute)
        while not finished.is_set():
            await runner.process_batch()
            await anyio.sleep(0.005)

    if failures:
        raise failures[0]
    return results[0]


async def test_every_native_operation_crosses_the_durable_worker_boundary(
    durable_components: tuple[QueuedSchematicAnalyzer, SchematicJobRunner, FakeSchematicAnalyzer, SchematicJobService],
) -> None:
    client, runner, native, _jobs = durable_components
    data = b"schematic"
    lattice = AutostackLattice(
        mode="1d",
        vectors=((1, 0, 0),),
        coverage=1.0,
        cell_min=(0, 0, 0),
        cell_max=(0, 0, 0),
        region_min=(0, 0, 0),
        region_max=(1, 0, 0),
    )

    assert (await _with_runner(runner, client.capabilities())).available is True
    assert await _with_runner(runner, client.analyze(data, limits=SchematicLimits())) == native.analysis
    assert await _with_runner(runner, client.convert(data, target=SchematicFormat.LITEMATIC)) == (b"converted", ())
    comparison = await _with_runner(runner, client.compare(data, data, preset=FingerprintPreset.SHAPE))
    assert comparison.identical is True
    assert (
        await _with_runner(
            runner,
            client.render(data, request=RenderRequest(), resource_pack=VerifiedResourcePack.from_bytes(b"pack")),
        )
        == native.render_output
    )
    assert await _with_runner(runner, client.simulate(data, request=SimulationRequest())) == native.simulation_output
    assert await _with_runner(runner, client.autostack(data, lattice=lattice, counts=(2,))) == b"stacked"


async def test_invalid_payloads_enter_a_retained_dead_state(
    schematic_job_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    del schematic_job_table
    config = SchematicConfig(job_poll_interval_seconds=0.005, job_wait_timeout_seconds=3)
    artifacts = LocalArtifactStore(tmp_path / "objects")
    jobs = SchematicJobService(PostgresSchematicJobRepository(async_session_factory))
    runner = SchematicJobRunner(jobs, artifacts, FakeSchematicAnalyzer(failure=InvalidSchematicError()), config)
    client = QueuedSchematicAnalyzer(jobs, artifacts, config)

    with pytest.raises(InvalidSchematicError):
        await _with_runner(runner, client.analyze(b"bad", limits=SchematicLimits()))

    async with async_session_factory() as session:
        job_id = await session.scalar(select(func.max(SchematicJob.id)))
    assert job_id is not None
    snapshot = await jobs.get(job_id)
    assert snapshot is not None
    assert snapshot.dead_at is not None
    assert snapshot.error_kind == "invalid"


async def test_ambiguous_simulation_inputs_survive_the_durable_worker_boundary(
    durable_components: tuple[QueuedSchematicAnalyzer, SchematicJobRunner, FakeSchematicAnalyzer, SchematicJobService],
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, runner, native, jobs = durable_components
    original = AmbiguousSimulationInputError(candidates=((12, 5, -3), (0, 1, 2)), rejected=(9, 9, 9))
    native.failure = original
    request = SimulationRequest(input_position=original.rejected)

    with pytest.raises(AmbiguousSimulationInputError) as raised:
        await _with_runner(runner, client.simulate(b"schematic", request=request))

    rebuilt = raised.value
    assert rebuilt.candidates == original.candidates
    assert rebuilt.rejected == original.rejected
    assert rebuilt.public_context == original.public_context
    assert rebuilt.public_detail() == original.public_detail()
    assert rebuilt.context["operation"] == "simulate"
    async with async_session_factory() as session:
        job_id = await session.scalar(select(func.max(SchematicJob.id)))
    assert job_id is not None
    assert rebuilt.context["job_id"] == job_id
    snapshot = await jobs.get(job_id)
    assert snapshot is not None
    assert snapshot.dead_at is not None
    assert snapshot.error_kind == "ambiguous_simulation_input"
    assert snapshot.error_context == {"candidates": [[0, 1, 2], [12, 5, -3]], "rejected": [9, 9, 9]}
    await runner.process_batch()
    assert native.simulate_calls == [(b"schematic", request)]


async def test_stale_workers_cannot_overwrite_or_delete_a_reclaimed_jobs_result(
    schematic_job_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    started, release = anyio.Event(), anyio.Event()

    class DelayedUpload(LocalArtifactStore):
        delayed: bool = False

        @override
        async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
            if key.startswith("schematic-jobs/results/") and not self.delayed:
                self.delayed = True
                started.set()
                await release.wait()
            return await super().put(key, data, content_type=content_type)

    artifacts = DelayedUpload(tmp_path / "objects")
    await artifacts.put("input", b"schematic", content_type="application/octet-stream")
    repository = PostgresSchematicJobRepository(async_session_factory)
    jobs = SchematicJobService(repository)
    job_id = await jobs.submit("render", {"request": wire.encode_render_request(RenderRequest())}, ("input",))
    stale_native = FakeSchematicAnalyzer()
    stale_native.render_output = b"stale render"
    stale = SchematicJobRunner(jobs, artifacts, stale_native, SchematicConfig())
    native = FakeSchematicAnalyzer()
    current = SchematicJobRunner(jobs, artifacts, native, SchematicConfig())

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(stale.process_batch)
            await started.wait()
            async with async_session_factory.begin() as session:
                await session.execute(
                    update(SchematicJob)
                    .where(SchematicJob.id == job_id)
                    .values(claimed_at=text("now() - interval '6 minutes'"))
                )
            await current.process_batch()
            completed = await jobs.get(job_id)
            assert completed is not None
            assert completed.completed_at is not None
            assert completed.result_object_key is not None
            release.set()

    assert await jobs.get(job_id) == completed
    assert await artifacts.get(completed.result_object_key, max_bytes=1024) == native.render_output

    async with async_session_factory.begin() as session:
        await session.execute(
            update(SchematicJob).where(SchematicJob.id == job_id).values(expires_at=text("now() - interval '1 hour'"))
        )
    await current.cleanup()
    assert await jobs.get(job_id) is None
    assert await artifacts.get(completed.result_object_key, max_bytes=1024) is None


@pytest.mark.parametrize("interruption", ["before_commit", "after_commit", "cancelled"])
async def test_attempt_outputs_survive_failed_acknowledgements_and_cleanup_retries(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    interruption: Literal["before_commit", "after_commit", "cancelled"],
) -> None:
    sessions = migrated_session_factory
    completion_started = anyio.Event()

    class InterruptedCompletion(PostgresSchematicJobRepository):
        interrupted: bool = False
        first_claim: ClaimedSchematicJob | None = None

        @override
        async def complete(
            self,
            job: ClaimedSchematicJob,
            result: Mapping[str, Any],
            result_object_key: str | None,
            *,
            retention_hours: int,
        ) -> bool:
            if not self.interrupted:
                self.interrupted = True
                self.first_claim = job
                if interruption == "cancelled":
                    completion_started.set()
                    await anyio.sleep_forever()
                if interruption == "after_commit":
                    await super().complete(job, result, result_object_key, retention_hours=retention_hours)
                raise OSError("completion acknowledgement lost")
            return await super().complete(job, result, result_object_key, retention_hours=retention_hours)

    class InterruptedDeletion(LocalArtifactStore):
        fail_key: str | None = None

        @override
        async def delete(self, key: str) -> None:
            if key == self.fail_key:
                self.fail_key = None
                raise OSError("object storage unavailable")
            await super().delete(key)

    artifacts = InterruptedDeletion(tmp_path / "objects")
    await artifacts.put("input", b"schematic", content_type="application/octet-stream")
    repository = InterruptedCompletion(sessions)
    jobs = SchematicJobService(repository)
    job_id = await jobs.submit("render", {"request": wire.encode_render_request(RenderRequest())}, ("input",))
    native = FakeSchematicAnalyzer()
    runner = SchematicJobRunner(jobs, artifacts, native, SchematicConfig())
    if interruption == "cancelled":
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(runner.process_batch)
                await completion_started.wait()
                tasks.cancel_scope.cancel()
    else:
        await runner.process_batch()

    async with sessions() as session:
        row = await session.get(SchematicJob, job_id)
        assert row is not None
        (first_key,) = row.result_object_keys
    assert await artifacts.get(first_key, max_bytes=1024) == native.render_output
    if interruption != "after_commit":
        async with sessions.begin() as session:
            await session.execute(
                update(SchematicJob)
                .where(SchematicJob.id == job_id)
                .values(available_at=func.now(), claimed_at=text("now() - interval '6 minutes'"))
            )
        await runner.process_batch()
    completed = await jobs.get(job_id)
    assert completed is not None
    assert completed.completed_at is not None
    assert completed.result_object_key is not None
    winning_key = completed.result_object_key
    assert (first_key == winning_key) is (interruption == "after_commit")
    assert await artifacts.get(winning_key, max_bytes=1024) == native.render_output
    assert repository.first_claim is not None
    assert not await jobs.register_result_object(repository.first_claim, "stale-output")

    # Old worker versions only wrote the winning key, without an attempt inventory.
    legacy_id = await jobs.submit("render", {}, ())
    await artifacts.put("legacy-output", b"legacy", content_type="image/png")
    async with sessions.begin() as session:
        await session.execute(
            update(SchematicJob)
            .where(SchematicJob.id == legacy_id)
            .values(result_object_key="legacy-output", completed_at=func.now())
        )
        await session.execute(
            update(SchematicJob)
            .where(SchematicJob.id.in_((job_id, legacy_id)))
            .values(expires_at=text("now() - interval '1 hour'"))
        )
    artifacts.fail_key = winning_key
    with pytest.raises(OSError, match="object storage unavailable"):
        await runner.cleanup()
    assert await jobs.get(job_id) is not None
    assert await jobs.get(legacy_id) is not None
    if first_key != winning_key:
        assert await artifacts.get(first_key, max_bytes=1024) is None
    await runner.cleanup()
    assert await jobs.get(job_id) is None
    assert await jobs.get(legacy_id) is None
    for key in {first_key, winning_key, "legacy-output"}:
        assert await artifacts.get(key, max_bytes=1024) is None
