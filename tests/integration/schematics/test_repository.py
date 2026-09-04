"""Integration coverage for schematic persistence.

Exercised against real PostgreSQL because the behaviours that matter here — content-addressed
upsert, the partial unique index enforcing one primary per build, and the version-scoped
fingerprint indexes — are all database semantics rather than Python ones.
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import anyio
import pytest
from sqlalchemy import Table, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

import squid.persistence.model_registry  # noqa: F401
from squid.accounts.infrastructure.models import Account
from squid.artifacts.infrastructure import LocalArtifactStore
from squid.persistence.base import Base
from squid.schematics.application import SchematicPublication
from squid.schematics.domain.models import (
    FingerprintPreset,
    SchematicFormat,
    SchematicLicense,
    SchematicVisibility,
    SimulationResult,
)
from squid.schematics.infrastructure.repository import SchematicRepository
from tests.unit.schematics.fakes import make_analysis

pytestmark = pytest.mark.asyncio

# `build_schematics` has a foreign key onto `builds`, so a stand-in table is created rather
# than dragging the entire build schema into a storage test.
_SETUP = (
    "CREATE TABLE IF NOT EXISTS builds (id BIGINT PRIMARY KEY, revision BIGINT NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS build_links ("
    "build_id BIGINT REFERENCES builds(id) ON DELETE CASCADE, url TEXT NOT NULL, media_type TEXT, "
    "PRIMARY KEY (build_id, url))",
    "INSERT INTO builds (id) VALUES (1), (2) ON CONFLICT DO NOTHING",
)
_TABLES: tuple[Table, ...] = (
    cast(Table, Account.__table__),
    Base.metadata.tables["schematic_files"],
    Base.metadata.tables["build_schematics"],
    Base.metadata.tables["schematic_render_queue"],
    Base.metadata.tables["schematic_renders"],
)


@pytest.fixture
async def schematic_tables(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    async with async_engine.begin() as connection:
        for statement in _SETUP:
            await connection.execute(text(statement))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield async_engine
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=tuple(reversed(_TABLES)))
            await connection.execute(text("DROP TABLE IF EXISTS build_links"))
            await connection.execute(text("DROP TABLE IF EXISTS builds"))


@pytest.fixture
def repository(
    schematic_tables: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> SchematicRepository:
    return SchematicRepository(async_session_factory, LocalArtifactStore(tmp_path / "objects"))


async def test_storing_the_same_bytes_twice_yields_one_row_and_one_digest(
    repository: SchematicRepository,
) -> None:
    """Content addressing is what makes "this is byte-identical to an existing submission"
    free to detect, so a resubmission must be an upsert rather than a conflict."""
    first = await repository.put_file(b"schematic-bytes", source_format=SchematicFormat.LITEMATIC)
    second = await repository.put_file(b"schematic-bytes", source_format=SchematicFormat.LITEMATIC)

    assert first == second
    assert await repository.get_file(first) == b"schematic-bytes"


async def test_new_payload_stores_only_object_metadata(
    repository: SchematicRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    digest = await repository.put_file(b"verified", source_format=SchematicFormat.LITEMATIC)

    async with async_session_factory() as session:
        state = (
            await session.execute(
                text("SELECT object_key IS NOT NULL FROM schematic_files WHERE sha256 = :digest"),
                {"digest": digest},
            )
        ).one()

    assert state == (True,)


async def test_re_analysing_a_file_replaces_its_row_rather_than_adding_one(
    repository: SchematicRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An engine upgrade re-analyses; accumulating a row per pass would corrupt every count."""
    digest = await repository.put_file(b"door", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, digest, make_analysis(block_count=10), primary=True)

    await repository.record_analysis(1, digest, make_analysis(block_count=99), primary=True)

    stored = await repository.list_for_build(1)
    assert len(stored) == 1
    assert stored[0].analysis.metrics.block_count == 99
    async with async_session_factory() as session:
        queued = await session.scalar(text("SELECT count(*) FROM schematic_render_queue WHERE build_id = 1"))
    assert queued == 1


async def test_uploader_account_attribution_reaches_the_persisted_attachment(
    repository: SchematicRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Provider-neutral account identity must survive the repository boundary."""
    async with async_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    digest = await repository.put_file(b"attributed", source_format=SchematicFormat.LITEMATIC)

    await repository.record_analysis(
        1,
        digest,
        make_analysis(),
        primary=True,
        uploaded_by_account_id=account_id,
    )

    async with async_session_factory() as session:
        stored_account_id = await session.scalar(
            text("SELECT uploaded_by_account_id FROM build_schematics WHERE build_id = 1 AND file_sha256 = :digest"),
            {"digest": digest},
        )
    assert stored_account_id == account_id


async def test_promoting_a_new_primary_demotes_the_previous_one(repository: SchematicRepository) -> None:
    """A partial unique index allows only one primary per build, so the swap has to happen in
    a single transaction or the second insert fails."""
    first = await repository.put_file(b"first", source_format=SchematicFormat.LITEMATIC)
    second = await repository.put_file(b"second", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, first, make_analysis(), primary=True, original_filename="first.litematic")

    await repository.record_analysis(1, second, make_analysis(), primary=True, original_filename="second.litematic")

    primary = await repository.get_featured(1)
    assert primary is not None
    assert primary.original_filename == "second.litematic"
    assert sum(stored.is_primary for stored in await repository.list_for_build(1)) == 1


async def test_replacing_a_primary_fences_its_render_and_replaces_the_projected_url(
    repository: SchematicRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_digest = await repository.put_file(b"first", source_format=SchematicFormat.LITEMATIC)
    second_digest = await repository.put_file(b"second", source_format=SchematicFormat.LITEMATIC)
    first_id = await repository.record_analysis(1, first_digest, make_analysis(), primary=True)
    manual_url = "https://media.example/user-managed-render.png"
    async with async_session_factory.begin() as session:
        await session.execute(
            text("INSERT INTO build_links (build_id, url, media_type) VALUES (1, :url, 'render')"),
            {"url": manual_url},
        )
    first_url = "https://api.example/v1/schematic-renders/first/content"
    first_render = await repository.publish_fresh_preview(
        first_id,
        "first-recipe",
        first_url,
        "renders/first.png",
        width=768,
        height=768,
        byte_size=42,
    )
    assert first_render is not None
    async with async_session_factory() as session:
        initial_links = set(
            await session.scalars(text("SELECT url FROM build_links WHERE build_id = 1 AND media_type = 'render'"))
        )
    assert initial_links == {manual_url, first_url}

    second_id = await repository.record_analysis(1, second_digest, make_analysis(), primary=True)
    async with async_session_factory() as session:
        links_after_replacement = set(
            await session.scalars(text("SELECT url FROM build_links WHERE build_id = 1 AND media_type = 'render'"))
        )
    assert links_after_replacement == {manual_url}
    stale_render = await repository.publish_fresh_preview(
        first_id,
        "stale-recipe",
        "https://api.example/v1/schematic-renders/stale/content",
        "renders/stale.png",
        width=768,
        height=768,
        byte_size=42,
    )
    assert stale_render is None
    assert await repository.publish_cached_preview(first_id, "first-recipe", first_url) is False

    second_url = "https://api.example/v1/schematic-renders/second/content"
    second_render = await repository.publish_fresh_preview(
        second_id,
        "second-recipe",
        second_url,
        "renders/second.png",
        width=768,
        height=768,
        byte_size=42,
    )
    assert second_render is not None
    replacement_url = "https://api.example/v1/schematic-renders/replacement/content"
    replacement = await repository.publish_fresh_preview(
        second_id,
        "replacement-recipe",
        replacement_url,
        "renders/replacement.png",
        width=768,
        height=768,
        byte_size=42,
    )
    assert replacement is not None

    async with async_session_factory() as session:
        links = set(
            await session.scalars(text("SELECT url FROM build_links WHERE build_id = 1 AND media_type = 'render'"))
        )
        stale_rows = await session.scalar(
            text("SELECT count(*) FROM schematic_renders WHERE recipe_hash = 'stale-recipe'")
        )

    assert links == {manual_url, replacement_url}
    assert stale_rows == 0


async def test_primary_replacement_wins_a_concurrent_render_publication(
    repository: SchematicRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_digest = await repository.put_file(b"first", source_format=SchematicFormat.LITEMATIC)
    second_digest = await repository.put_file(b"second", source_format=SchematicFormat.LITEMATIC)
    first_id = await repository.record_analysis(1, first_digest, make_analysis(), primary=True)
    second_id = await repository.record_analysis(1, second_digest, make_analysis(), primary=False)

    published = anyio.Event()
    results: list[object | None] = []

    async def publish_late_render() -> None:
        results.append(
            await repository.publish_fresh_preview(
                first_id,
                "late-recipe",
                "https://api.example/v1/schematic-renders/late/content",
                "renders/late.png",
                width=768,
                height=768,
                byte_size=42,
            )
        )
        published.set()

    async with anyio.create_task_group() as tasks:
        async with async_session_factory() as replacement, replacement.begin():
            await replacement.execute(text("SELECT id FROM builds WHERE id = 1 FOR UPDATE"))
            tasks.start_soon(publish_late_render)
            with anyio.move_on_after(0.1) as scope:
                await published.wait()
            assert scope.cancelled_caught
            await replacement.execute(
                text("UPDATE build_schematics SET is_primary = false WHERE id = :schematic_id"),
                {"schematic_id": first_id},
            )
            await replacement.execute(
                text("UPDATE build_schematics SET is_primary = true WHERE id = :schematic_id"),
                {"schematic_id": second_id},
            )
        await published.wait()

    assert results == [None]
    async with async_session_factory() as session:
        stale_rows = await session.scalar(
            text("SELECT count(*) FROM schematic_renders WHERE recipe_hash = 'late-recipe'")
        )
    assert stale_rows == 0


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


async def test_publication_round_trips_and_reanalysis_does_not_reset_it(
    repository: SchematicRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = Instant.parse_iso("2026-08-11T12:00:00Z")
    async with async_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    digest = await repository.put_file(b"public", source_format=SchematicFormat.SPONGE_SCHEM)
    publication = SchematicPublication(
        visibility=SchematicVisibility.PUBLIC_DOWNLOAD,
        license=SchematicLicense.CC_BY_4_0,
        rights_attested_at=now,
        rights_attested_by_account_id=account_id,
        sanitized_at=now,
        sanitizer_version="nucleation-test",
        sanitization_report={"removed": 0},
        published_at=now,
    )
    schematic_id = await repository.record_analysis(
        1,
        digest,
        make_analysis(),
        primary=True,
        publication=publication,
    )

    await repository.record_analysis(1, digest, make_analysis(block_count=99), primary=True)
    stored = await repository.get_for_build(1, schematic_id)

    assert stored is not None
    assert stored.publication == publication
    assert stored.analysis.metrics.block_count == 99


async def test_oversized_bytes_are_refused_by_the_database_as_well_as_the_upload_check(
    repository: SchematicRepository,
) -> None:
    """Defence in depth: the size cap is a check constraint, not only an application rule."""
    with pytest.raises(IntegrityError):
        await repository.put_file(b"\x00" * (16 * 1024 * 1024 + 1), source_format=SchematicFormat.LITEMATIC)


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

    stored = await repository.get_featured(1)
    assert stored is not None
    assert stored.simulation_evidence == evidence
    assert stored.analysis.metrics.block_count == make_analysis().metrics.block_count
