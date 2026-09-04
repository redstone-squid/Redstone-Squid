"""Integration coverage for the supervised schematic worker pool.

These exercise the two properties the whole subprocess design exists for: that the engine's
answers are the ones the duplicate detector depends on, and that when the engine dies the bot
does not.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from importlib.metadata import version

import pytest

from squid.config import SchematicConfig
from squid.schematics.application.commands import SimulationRequest
from squid.schematics.domain.models import FingerprintPreset, SchematicFormat, SchematicLimits
from squid.schematics.errors import (
    AmbiguousSimulationInputError,
    InvalidSchematicError,
    SchematicSupportUnavailableError,
    SchematicTimeoutError,
    SchematicTooLargeError,
    SchematicWorkerCrashedError,
)
from squid.schematics.infrastructure.worker import SchematicWorkerPool

pytestmark = [pytest.mark.schematic, pytest.mark.asyncio]


@pytest.fixture
async def pool() -> AsyncIterator[SchematicWorkerPool]:
    """Hold the pool's pump lifetime in a task that spans setup and teardown.

    pytest-asyncio runs a fixture's setup and its finalization in two different tasks, and an
    anyio task group can only be exited by the task that entered it. So the lifetime belongs to
    an owner task here, the same way it belongs to `main()` in the worker process.
    """
    worker_pool = SchematicWorkerPool(SchematicConfig(workers=1, restart_backoff_seconds=0.05))
    running, finished = asyncio.Event(), asyncio.Event()

    async def own() -> None:
        async with worker_pool.running():
            running.set()
            await finished.wait()

    owner = asyncio.create_task(own())
    await running.wait()
    try:
        yield worker_pool
    finally:
        finished.set()
        await owner


async def test_the_pool_reports_the_engine_it_actually_loaded(pool: SchematicWorkerPool) -> None:
    """The worker must report the engine *this* environment installed.

    Fingerprints are version-scoped, so a worker quietly running a different build than
    its parent would poison duplicate lookups. Which version that is, is pyproject's exact
    pin to state; repeating the literal here only guaranteed a failure on every bump.
    """
    capabilities = await pool.capabilities()

    assert capabilities.available is True
    assert capabilities.analyzer_version == f"nucleation-{version('nucleation')}"
    assert capabilities.can_simulate is True


async def test_analysis_reads_tight_dimensions_not_allocated_bounds(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    """`dimensions()` reports the region the file allocates; only `tight_dimensions()` is a
    measurement of the build, and it is the one a record can be argued from."""
    analysis = await pool.analyze(periodic_door(), limits=SchematicLimits(), source_format=SchematicFormat.LITEMATIC)

    metrics = analysis.metrics
    assert (metrics.dimensions.width, metrics.dimensions.height, metrics.dimensions.length) == (22, 3, 1)
    assert metrics.block_count == 24
    assert metrics.bounding_volume == metrics.dimensions.volume


@pytest.mark.parametrize(
    "source_format",
    [SchematicFormat.LITEMATIC, SchematicFormat.SPONGE_SCHEM, SchematicFormat.MCSTRUCTURE],
)
async def test_each_native_generated_input_format_keeps_its_vetted_source_format(
    pool: SchematicWorkerPool,
    native_format_exports: dict[SchematicFormat, bytes],
    source_format: SchematicFormat,
) -> None:
    analysis = await pool.analyze(
        native_format_exports[source_format],
        limits=SchematicLimits(),
        source_format=source_format,
    )

    assert analysis.metrics.source_format is source_format
    assert analysis.metrics.block_count == 1


async def test_the_shape_fingerprint_survives_translation(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    """The load-bearing assumption for duplicate detection: the same build reposted at other
    coordinates must hash the same."""
    here = await pool.analyze(periodic_door(), limits=SchematicLimits())
    moved = await pool.analyze(periodic_door(offset=13), limits=SchematicLimits())

    assert here.fingerprints.shape == moved.fingerprints.shape
    assert here.fingerprints.exact == moved.fingerprints.exact


async def test_shape_separates_builds_that_structural_lumps_together(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    """`structural` is a coarse bucket: one added glass block still matches. That is why the
    duplicate index is `shape` and `structural` is only ever a pre-filter."""
    original, altered = periodic_door(), periodic_door(extra_block=True)

    structural = await pool.compare(original, altered, preset=FingerprintPreset.STRUCTURAL)
    shape = await pool.compare(original, altered, preset=FingerprintPreset.SHAPE)

    assert structural.identical is True
    assert shape.identical is False
    assert shape.footprint_distance > 0


async def test_repeating_structure_detection_recovers_the_period(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    analysis = await pool.analyze(periodic_door(), limits=SchematicLimits(), with_lattice=True)

    assert analysis.lattice is not None
    assert (4, 0, 0) in analysis.lattice.vectors


async def test_autostack_round_trip_at_the_original_counts_preserves_the_build(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    original = periodic_door()
    analysis = await pool.analyze(original, limits=SchematicLimits(), with_lattice=True)
    assert analysis.lattice is not None

    resized = await pool.autostack(original, lattice=analysis.lattice, counts=(6, 1))
    comparison = await pool.compare(original, resized, preset=FingerprintPreset.EXACT)

    assert comparison.identical is True


async def test_tick_simulation_moves_a_piston_and_settles(pool: SchematicWorkerPool, piston_door: bytes) -> None:
    result = await pool.simulate(piston_door, request=SimulationRequest())

    assert result.input_position == (0, 1, 0)
    assert result.input_source == "heuristic"
    assert result.settled_tick is not None
    assert result.last_piston_tick is not None
    assert result.piston_events > 0
    assert result.trustworthy is True


async def test_tick_simulation_prefers_an_insign_input_when_controls_are_ambiguous(
    pool: SchematicWorkerPool, insign_piston_door: bytes
) -> None:
    result = await pool.simulate(insign_piston_door, request=SimulationRequest())

    assert result.input_position == (0, 1, 0)
    assert result.input_source == "insign"
    assert result.piston_events > 0


async def test_a_named_input_overrides_the_insign_annotation(
    pool: SchematicWorkerPool, insign_piston_door: bytes
) -> None:
    """A moderator naming a coordinate is answering the question the annotation answers.

    Preferring the annotation anyway would time the door's button while reporting a result for
    the command they ran against the other one.
    """
    result = await pool.simulate(insign_piston_door, request=SimulationRequest(input_position=(5, 1, 0)))

    assert result.input_position == (5, 1, 0)
    assert result.input_source == "manual"


async def test_a_named_input_that_is_not_a_control_comes_back_with_the_ones_that_are(
    pool: SchematicWorkerPool, insign_piston_door: bytes
) -> None:
    """The candidates have to survive the worker pipe, or the refusal is unactionable."""
    with pytest.raises(AmbiguousSimulationInputError) as raised:
        await pool.simulate(insign_piston_door, request=SimulationRequest(input_position=(2, 1, 0)))

    assert raised.value.rejected == (2, 1, 0)
    assert raised.value.candidates == ((0, 1, 0), (5, 1, 0))
    assert raised.value.public_context["input_candidates"] == [[0, 1, 0], [5, 1, 0]]


@pytest.mark.parametrize(
    "target",
    [SchematicFormat.LITEMATIC, SchematicFormat.SPONGE_SCHEM, SchematicFormat.MCSTRUCTURE],
)
async def test_each_native_export_format_round_trips_the_build(
    pool: SchematicWorkerPool,
    periodic_door: Callable[..., bytes],
    target: SchematicFormat,
) -> None:
    original = periodic_door()
    converted, losses = await pool.convert(original, target=target)

    before = await pool.analyze(original, limits=SchematicLimits())
    after = await pool.analyze(converted, limits=SchematicLimits(), source_format=target)

    assert losses == ()
    assert after.metrics.block_count == before.metrics.block_count
    assert after.metrics.dimensions == before.metrics.dimensions


async def test_a_schematic_larger_than_the_budget_is_refused_by_the_worker(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    with pytest.raises(SchematicTooLargeError) as raised:
        await pool.analyze(periodic_door(), limits=SchematicLimits(max_allocated_volume=4))

    assert raised.value.measure == "allocated volume"

    with pytest.raises(SchematicTooLargeError) as axis_raised:
        await pool.analyze(periodic_door(), limits=SchematicLimits(max_axis_length=20))

    assert axis_raised.value.measure == "allocated axis length"


async def test_corrupt_bytes_come_back_as_a_typed_error_and_the_pool_keeps_serving(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    with pytest.raises(InvalidSchematicError):
        await pool.analyze(b"\x1f\x8bnot really a schematic", limits=SchematicLimits())

    assert (await pool.analyze(periodic_door(), limits=SchematicLimits())).metrics.block_count == 24


async def test_a_worker_killed_mid_request_is_replaced_and_the_bot_survives(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes], slow_schematic: bytes
) -> None:
    """The test that proves the isolation design: a dead engine is one failed request, not an
    outage. Nothing is retried, because a payload that killed a worker would kill the next."""
    await pool.capabilities()
    process = pool._workers[0]._process
    assert process is not None

    in_flight = asyncio.create_task(pool.analyze(slow_schematic, limits=SchematicLimits(), with_lattice=True))
    await asyncio.sleep(0.2)
    process.kill()

    with pytest.raises(SchematicWorkerCrashedError):
        await in_flight

    assert (await pool.analyze(periodic_door(), limits=SchematicLimits())).metrics.block_count == 24


async def test_a_clean_shutdown_does_not_look_like_a_failure(
    pool: SchematicWorkerPool, caplog: pytest.LogCaptureFixture
) -> None:
    """Operators page on WARNING, so an orderly stop must not emit one."""
    await pool.capabilities()

    with caplog.at_level(logging.INFO, logger="squid.schematics.infrastructure.worker"):
        await pool.aclose()

    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING] == []
    assert any("exited with code 0" in record.getMessage() for record in caplog.records)


async def test_an_operation_past_its_deadline_is_killed_rather_than_left_running(
    slow_schematic: bytes,
) -> None:
    pool = SchematicWorkerPool(
        SchematicConfig(
            workers=1, parse_timeout_seconds=0.001, convert_timeout_seconds=30, restart_backoff_seconds=0.05
        )
    )
    async with pool.running():
        with pytest.raises(SchematicTimeoutError):
            await pool.analyze(slow_schematic, limits=SchematicLimits(), with_lattice=True)

        # A fresh worker is spawned, so an operation with a workable deadline still succeeds.
        converted, _ = await pool.convert(slow_schematic, target=SchematicFormat.SPONGE_SCHEM)
        assert converted


async def test_repeated_crashes_trip_the_circuit_breaker_instead_of_spawning_forever(
    slow_schematic: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """A user who keeps retrying a poison file must not be able to fork-bomb the host."""
    pool = SchematicWorkerPool(
        SchematicConfig(
            workers=1,
            parse_timeout_seconds=0.001,
            restart_backoff_seconds=0.01,
            max_restarts_per_window=2,
        )
    )
    async with pool.running():
        with caplog.at_level(logging.ERROR, logger="squid.schematics.infrastructure.worker"):
            for _ in range(2):
                with pytest.raises(SchematicTimeoutError):
                    await pool.analyze(slow_schematic, limits=SchematicLimits(), with_lattice=True)

            with pytest.raises(SchematicSupportUnavailableError):
                await pool.analyze(slow_schematic, limits=SchematicLimits(), with_lattice=True)

        # Operators without an OTel exporter must still see the outage in the logs.
        assert [record.getMessage() for record in caplog.records if "circuit breaker opened" in record.getMessage()]
