"""Real-PostgreSQL coverage for durable schematic execution."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable
from pathlib import Path
from typing import TypeVar

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.artifacts.infrastructure import LocalArtifactStore
from squid.config import SchematicConfig
from squid.persistence.base import Base
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.application.jobs import SchematicJobService
from squid.schematics.domain.models import AutostackLattice, FingerprintPreset, SchematicFormat, SchematicLimits
from squid.schematics.errors import InvalidSchematicError
from squid.schematics.infrastructure.durable import QueuedSchematicAnalyzer, SchematicJobRunner
from squid.schematics.infrastructure.jobs import PostgresSchematicJobRepository
from squid.schematics.infrastructure.models import SchematicJob
from tests.unit.schematics.fakes import FakeSchematicAnalyzer

pytestmark = pytest.mark.asyncio

ResultT = TypeVar("ResultT")
_TABLE = Base.metadata.tables["schematic_jobs"]


@pytest.fixture
async def schematic_job_table(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
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


async def _with_runner(runner: SchematicJobRunner, request: Awaitable[ResultT]) -> ResultT:
    task = asyncio.create_task(request)
    while not task.done():
        await runner.process_batch()
        await asyncio.sleep(0.005)
    return await task


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
            client.render(data, request=RenderRequest(), resource_pack=b"pack"),
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


async def test_stale_workers_cannot_complete_a_reclaimed_job(
    schematic_job_table: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del schematic_job_table
    repository = PostgresSchematicJobRepository(async_session_factory)
    job_id = await repository.submit("capabilities", {}, ())
    first = (await repository.claim(limit=1))[0]
    async with async_session_factory.begin() as session:
        await session.execute(
            update(SchematicJob)
            .where(SchematicJob.id == job_id)
            .values(claimed_at=first.claimed_at.subtract(minutes=6))
        )
    second = (await repository.claim(limit=1))[0]

    assert await repository.complete(first, {}, None, retention_hours=1) is False
    assert await repository.complete(second, {}, None, retention_hours=1) is True
