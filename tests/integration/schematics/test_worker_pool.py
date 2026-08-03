"""Integration coverage for the supervised schematic worker pool.

These exercise the two properties the whole subprocess design exists for: that the engine's
answers are the ones the duplicate detector depends on, and that when the engine dies the bot
does not.
"""

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from squid.config import SchematicConfig
from squid.schematics.domain.models import FingerprintPreset, SchematicFormat, SchematicLimits
from squid.schematics.errors import (
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
    worker_pool = SchematicWorkerPool(SchematicConfig(workers=1, restart_backoff_seconds=0.05))
    try:
        yield worker_pool
    finally:
        await worker_pool.aclose()


async def test_the_pool_reports_the_engine_it_actually_loaded(pool: SchematicWorkerPool) -> None:
    capabilities = await pool.capabilities()

    assert capabilities.available is True
    assert capabilities.analyzer_version is not None
    assert capabilities.analyzer_version.startswith("nucleation-")


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


async def test_a_round_trip_through_another_format_preserves_the_build(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    original = periodic_door()
    converted, losses = await pool.convert(original, target=SchematicFormat.SPONGE_SCHEM)

    before = await pool.analyze(original, limits=SchematicLimits())
    after = await pool.analyze(converted, limits=SchematicLimits())

    assert losses == ()
    assert after.metrics.block_count == before.metrics.block_count
    assert after.metrics.dimensions == before.metrics.dimensions


async def test_a_schematic_larger_than_the_budget_is_refused_by_the_worker(
    pool: SchematicWorkerPool, periodic_door: Callable[..., bytes]
) -> None:
    with pytest.raises(SchematicTooLargeError) as raised:
        await pool.analyze(periodic_door(), limits=SchematicLimits(max_allocated_volume=4))

    assert raised.value.measure == "allocated volume"


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


async def test_an_operation_past_its_deadline_is_killed_rather_than_left_running(
    slow_schematic: bytes,
) -> None:
    pool = SchematicWorkerPool(
        SchematicConfig(
            workers=1, parse_timeout_seconds=0.001, convert_timeout_seconds=30, restart_backoff_seconds=0.05
        )
    )
    try:
        with pytest.raises(SchematicTimeoutError):
            await pool.analyze(slow_schematic, limits=SchematicLimits(), with_lattice=True)

        # A fresh worker is spawned, so an operation with a workable deadline still succeeds.
        converted, _ = await pool.convert(slow_schematic, target=SchematicFormat.SPONGE_SCHEM)
        assert converted
    finally:
        await pool.aclose()


async def test_repeated_crashes_trip_the_circuit_breaker_instead_of_spawning_forever(
    slow_schematic: bytes,
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
    try:
        for _ in range(2):
            with pytest.raises(SchematicTimeoutError):
                await pool.analyze(slow_schematic, limits=SchematicLimits(), with_lattice=True)

        with pytest.raises(SchematicSupportUnavailableError):
            await pool.analyze(slow_schematic, limits=SchematicLimits(), with_lattice=True)
    finally:
        await pool.aclose()
