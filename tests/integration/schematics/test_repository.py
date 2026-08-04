"""Integration coverage for schematic persistence.

Exercised against real PostgreSQL because the behaviours that matter here — content-addressed
upsert, the partial unique index enforcing one primary per build, and the version-scoped
fingerprint indexes — are all database semantics rather than Python ones.
"""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.persistence.base import Base
from squid.schematics.domain.models import FingerprintPreset, SchematicFormat, SimulationResult
from squid.schematics.infrastructure.repository import SchematicRepository
from tests.unit.schematics.fakes import make_analysis

pytestmark = pytest.mark.asyncio

# `build_schematics` has a foreign key onto `builds`, so a stand-in table is created rather
# than dragging the entire build schema into a storage test.
_SETUP = (
    "CREATE TABLE IF NOT EXISTS builds (id BIGINT PRIMARY KEY)",
    "INSERT INTO builds (id) VALUES (1), (2) ON CONFLICT DO NOTHING",
)
_TABLES = [Base.metadata.tables["schematic_files"], Base.metadata.tables["build_schematics"]]


@pytest.fixture
async def schematic_tables(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine, None]:
    async with async_engine.begin() as connection:
        for statement in _SETUP:
            await connection.execute(text(statement))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield async_engine
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))
            await connection.execute(text("DROP TABLE IF EXISTS builds"))


@pytest.fixture
def repository(
    schematic_tables: AsyncEngine, async_session_factory: async_sessionmaker[AsyncSession]
) -> SchematicRepository:
    return SchematicRepository(async_session_factory)


async def test_storing_the_same_bytes_twice_yields_one_row_and_one_digest(
    repository: SchematicRepository,
) -> None:
    """Content addressing is what makes "this is byte-identical to an existing submission"
    free to detect, so a resubmission must be an upsert rather than a conflict."""
    first = await repository.put_file(b"schematic-bytes", source_format=SchematicFormat.LITEMATIC)
    second = await repository.put_file(b"schematic-bytes", source_format=SchematicFormat.LITEMATIC)

    assert first == second
    assert await repository.get_file(first) == b"schematic-bytes"


async def test_re_analysing_a_file_replaces_its_row_rather_than_adding_one(
    repository: SchematicRepository,
) -> None:
    """An engine upgrade re-analyses; accumulating a row per pass would corrupt every count."""
    digest = await repository.put_file(b"door", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, digest, make_analysis(block_count=10), primary=True)

    await repository.record_analysis(1, digest, make_analysis(block_count=99), primary=True)

    stored = await repository.list_for_build(1)
    assert len(stored) == 1
    assert stored[0].analysis.metrics.block_count == 99


async def test_promoting_a_new_primary_demotes_the_previous_one(repository: SchematicRepository) -> None:
    """A partial unique index allows only one primary per build, so the swap has to happen in
    a single transaction or the second insert fails."""
    first = await repository.put_file(b"first", source_format=SchematicFormat.LITEMATIC)
    second = await repository.put_file(b"second", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, first, make_analysis(), primary=True, original_filename="first.litematic")

    await repository.record_analysis(1, second, make_analysis(), primary=True, original_filename="second.litematic")

    primary = await repository.get_primary(1)
    assert primary is not None
    assert primary.original_filename == "second.litematic"
    assert sum(stored.is_primary for stored in await repository.list_for_build(1)) == 1


async def test_a_fingerprint_lookup_finds_the_same_build_resubmitted_under_another_id(
    repository: SchematicRepository,
) -> None:
    shared = make_analysis(shape="translated-shape")
    for build_id, payload in ((1, b"original"), (2, b"moved")):
        digest = await repository.put_file(payload, source_format=SchematicFormat.LITEMATIC)
        await repository.record_analysis(build_id, digest, shared, primary=True)

    matches = await repository.find_fingerprint_matches(
        "translated-shape",
        preset=FingerprintPreset.SHAPE,
        analyzer_version=shared.analyzer_version,
        exclude_build_id=2,
    )

    assert [match.build_id for match in matches] == [1]


async def test_an_exact_file_lookup_finds_every_build_using_the_same_bytes(
    repository: SchematicRepository,
) -> None:
    digest = await repository.put_file(b"shared", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, digest, make_analysis(), primary=True)
    await repository.record_analysis(2, digest, make_analysis(), primary=True)

    matches = await repository.find_file_matches(digest, exclude_build_id=2)

    assert [match.build_id for match in matches] == [1]


async def test_a_fingerprint_from_another_engine_version_never_matches(
    repository: SchematicRepository,
) -> None:
    """Fingerprints are hashes whose definition can change between releases. Comparing across
    versions would return confident garbage, so the version is part of every lookup."""
    digest = await repository.put_file(b"original", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, digest, make_analysis(shape="s", analyzer_version="engine-1"), primary=True)

    assert (
        await repository.find_fingerprint_matches("s", preset=FingerprintPreset.SHAPE, analyzer_version="engine-2")
        == []
    )


async def test_metric_neighbours_shortlist_builds_of_comparable_size(
    repository: SchematicRepository,
) -> None:
    close = make_analysis(dimensions=(3, 4, 5), block_count=100)
    far = make_analysis(dimensions=(80, 90, 100), block_count=100_000)
    for build_id, payload, analysis in ((1, b"close", close), (2, b"far", far)):
        digest = await repository.put_file(payload, source_format=SchematicFormat.LITEMATIC)
        await repository.record_analysis(build_id, digest, analysis, primary=True)

    neighbours = await repository.find_metric_neighbours(close.metrics, tolerance=0.2, exclude_build_id=99)

    assert [neighbour.build_id for neighbour in neighbours] == [1]


async def test_the_stored_read_model_carries_the_file_facts_from_the_file_row(
    repository: SchematicRepository,
) -> None:
    """`source_format` and `byte_size` describe the bytes, not the analysis, so they live on
    the content-addressed row and are joined back in rather than duplicated per attachment."""
    digest = await repository.put_file(b"sponge-bytes", source_format=SchematicFormat.SPONGE_SCHEM)
    await repository.record_analysis(1, digest, make_analysis(), primary=True)

    stored = (await repository.list_for_build(1))[0]

    assert stored.analysis.metrics.source_format is SchematicFormat.SPONGE_SCHEM
    assert stored.analysis.metrics.byte_size == len(b"sponge-bytes")


async def test_oversized_bytes_are_refused_by_the_database_as_well_as_the_upload_check(
    repository: SchematicRepository,
) -> None:
    """Defence in depth: the size cap is a check constraint, not only an application rule."""
    with pytest.raises(IntegrityError):
        await repository.put_file(b"\x00" * (2 * 1024 * 1024 + 1), source_format=SchematicFormat.LITEMATIC)


async def test_simulation_evidence_round_trips_without_changing_the_analysis(
    repository: SchematicRepository,
) -> None:
    digest = await repository.put_file(b"door", source_format=SchematicFormat.LITEMATIC)
    schematic_id = await repository.record_analysis(1, digest, make_analysis(), primary=True)
    evidence = SimulationResult(
        ticks_run=9,
        settled_tick=9,
        input_position=(7, 3, 0),
        input_source="heuristic",
        last_piston_tick=8,
        block_changes=621,
        piston_events=30,
        redstone_events=60,
        trustworthy=True,
    )

    await repository.record_simulation(schematic_id, evidence)

    stored = await repository.get_primary(1)
    assert stored is not None
    assert stored.simulation_evidence == evidence
    assert stored.analysis.metrics.block_count == make_analysis().metrics.block_count
