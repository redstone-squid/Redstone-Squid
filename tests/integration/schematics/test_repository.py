"""Schematic storage and preview publication against PostgreSQL.

Exercise content-addressed files, one primary attachment per build, version-scoped
fingerprint lookups, and coordination between preview publication and storage cleanup.
"""

import hashlib
from collections.abc import AsyncGenerator
from functools import partial
from pathlib import Path
from typing import override

import anyio
import pytest
from sqlalchemy import Table, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

import squid.persistence.model_registry  # noqa: F401
from squid.accounts.models import Account
from squid.artifacts import ArtifactMetadata
from squid.artifacts.infrastructure import LocalArtifactStore
from squid.core.errors import DataIntegrityError
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.schematics.application import SchematicPublication
from squid.schematics.application.preview_storage import PreviewCleanupInProgressError, SchematicPreviewStorage
from squid.schematics.application.storage import SchematicStorageService
from squid.schematics.domain.models import (
    SCHEMATIC_FILE_SCHEMA_MAX_BYTES,
    FingerprintPreset,
    SchematicFormat,
    SchematicLicense,
    SchematicVisibility,
    SimulationResult,
)
from tests.support.schema import model_tables
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
    *model_tables(Account),
    Base.metadata.tables["schematic_files"],
    Base.metadata.tables["build_schematics"],
    Base.metadata.tables["schematic_render_queue"],
    Base.metadata.tables["schematic_preview_objects"],
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
def preview_publisher(
    schematic_tables: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
    preview_artifacts: LocalArtifactStore,
) -> SchematicPreviewStorage:
    return SchematicPreviewStorage(preview_artifacts, uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))


@pytest.fixture
def preview_artifacts(schematic_tables: AsyncEngine, tmp_path: Path) -> LocalArtifactStore:
    del schematic_tables
    return LocalArtifactStore(tmp_path / "objects")


@pytest.fixture
def repository(
    schematic_tables: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
    preview_artifacts: LocalArtifactStore,
    preview_publisher: SchematicPreviewStorage,
) -> SchematicStorageService:
    return SchematicStorageService(preview_artifacts, uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))


async def _ready_preview_object(
    publisher: SchematicPreviewStorage,
    artifacts: LocalArtifactStore,
    object_key: str,
    *,
    byte_size: int = 42,
) -> None:
    content = b"p" * byte_size
    reservation = await publisher.reserve_preview_object(
        object_key,
        byte_size=byte_size,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    if reservation.upload_required:
        await artifacts.put(object_key, content, content_type="image/png")
        await publisher.mark_preview_object_ready(reservation)


async def test_storing_the_same_bytes_twice_yields_one_row_and_one_digest(
    repository: SchematicStorageService,
) -> None:
    """Repeated uploads of identical bytes must reuse their content-addressed file."""
    first = await repository.put_file(b"schematic-bytes", source_format=SchematicFormat.LITEMATIC)
    second = await repository.put_file(b"schematic-bytes", source_format=SchematicFormat.LITEMATIC)

    assert first == second
    assert await repository.get_file(first) == b"schematic-bytes"


async def test_new_payload_stores_only_object_metadata(
    repository: SchematicStorageService,
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
    repository: SchematicStorageService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reanalysis updates the existing attachment rather than accumulating duplicate measurements."""
    digest = await repository.put_file(b"door", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, digest, make_analysis(block_count=10), primary=True)

    await repository.record_analysis(1, digest, make_analysis(block_count=59), primary=True)

    stored = await repository.list_for_build(1)
    assert len(stored) == 1
    assert stored[0].analysis.metrics.block_count == 59
    async with async_session_factory() as session:
        queued = await session.scalar(text("SELECT count(*) FROM schematic_render_queue WHERE build_id = 1"))
    assert queued == 1


async def test_uploader_account_attribution_reaches_the_persisted_attachment(
    repository: SchematicStorageService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
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


async def test_promoting_a_new_primary_demotes_the_previous_one(repository: SchematicStorageService) -> None:
    """Demote the old primary before promoting its replacement to satisfy the partial unique index."""
    first = await repository.put_file(b"first", source_format=SchematicFormat.LITEMATIC)
    second = await repository.put_file(b"second", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, first, make_analysis(), primary=True, original_filename="first.litematic")

    await repository.record_analysis(1, second, make_analysis(), primary=True, original_filename="second.litematic")

    primary = await repository.get_featured(1)
    assert primary is not None
    assert primary.original_filename == "second.litematic"
    assert sum(stored.is_primary for stored in await repository.list_for_build(1)) == 1


async def test_replacing_a_featured_attachment_fences_its_render_and_replaces_the_generated_url(
    repository: SchematicStorageService,
    preview_publisher: SchematicPreviewStorage,
    preview_artifacts: LocalArtifactStore,
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
    await _ready_preview_object(preview_publisher, preview_artifacts, "renders/first.png")
    first_render = await preview_publisher.publish_fresh_preview(
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
    await _ready_preview_object(preview_publisher, preview_artifacts, "renders/stale.png")
    stale_render = await preview_publisher.publish_fresh_preview(
        first_id,
        "stale-recipe",
        "https://api.example/v1/schematic-renders/stale/content",
        "renders/stale.png",
        width=768,
        height=768,
        byte_size=42,
    )
    assert stale_render is None
    assert await preview_publisher.publish_cached_preview(first_id, "first-recipe", first_url) is False

    second_url = "https://api.example/v1/schematic-renders/second/content"
    await _ready_preview_object(preview_publisher, preview_artifacts, "renders/second.png")
    second_render = await preview_publisher.publish_fresh_preview(
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
    await _ready_preview_object(preview_publisher, preview_artifacts, "renders/replacement.png")
    replacement = await preview_publisher.publish_fresh_preview(
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
    repository: SchematicStorageService,
    preview_publisher: SchematicPreviewStorage,
    preview_artifacts: LocalArtifactStore,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_digest = await repository.put_file(b"first", source_format=SchematicFormat.LITEMATIC)
    second_digest = await repository.put_file(b"second", source_format=SchematicFormat.LITEMATIC)
    first_id = await repository.record_analysis(1, first_digest, make_analysis(), primary=True)
    await repository.record_analysis(1, second_digest, make_analysis(), primary=False)
    await _ready_preview_object(preview_publisher, preview_artifacts, "renders/late.png")

    results: list[object | None] = []
    start = anyio.Event()

    async def publish_late_render() -> None:
        await start.wait()
        results.append(
            await preview_publisher.publish_fresh_preview(
                first_id,
                "late-recipe",
                "https://api.example/v1/schematic-renders/late/content",
                "renders/late.png",
                width=768,
                height=768,
                byte_size=42,
            )
        )

    async def replace_primary() -> None:
        await start.wait()
        await repository.record_analysis(1, second_digest, make_analysis(), primary=True)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(publish_late_render)
            tasks.start_soon(replace_primary)
            start.set()

    assert len(results) == 1
    featured = await repository.get_featured(1)
    assert featured is not None
    assert featured.file_sha256 == second_digest
    async with async_session_factory() as session:
        generated_links = set(
            await session.scalars(
                text(
                    "SELECT url FROM build_links "
                    "WHERE build_id = 1 AND media_type = 'render' "
                    "AND url = 'https://api.example/v1/schematic-renders/late/content'"
                )
            )
        )
        queued = await session.scalar(text("SELECT count(*) FROM schematic_render_queue WHERE build_id = 1"))
    assert generated_links == set()
    assert queued == 1


async def test_republishing_the_same_recipe_replaces_its_prior_generated_url(
    repository: SchematicStorageService,
    preview_publisher: SchematicPreviewStorage,
    preview_artifacts: LocalArtifactStore,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    digest = await repository.put_file(b"same-recipe", source_format=SchematicFormat.LITEMATIC)
    schematic_id = await repository.record_analysis(1, digest, make_analysis(), primary=True)
    object_key = "renders/same-recipe.png"
    await _ready_preview_object(preview_publisher, preview_artifacts, object_key)

    first = await preview_publisher.publish_fresh_preview(
        schematic_id,
        "same-recipe",
        "https://old.example/v1/schematic-renders/same-recipe/content",
        object_key,
        width=768,
        height=768,
        byte_size=42,
    )
    second = await preview_publisher.publish_fresh_preview(
        schematic_id,
        "same-recipe",
        "https://new.example/v1/schematic-renders/same-recipe/content",
        object_key,
        width=768,
        height=768,
        byte_size=42,
    )

    assert first is not None
    assert second is not None
    async with async_session_factory() as session:
        links = set(await session.scalars(text("SELECT url FROM build_links WHERE build_id = 1")))
    assert links == {"https://new.example/v1/schematic-renders/same-recipe/content"}


async def test_preview_cleanup_retains_an_object_referenced_by_multiple_builds(
    repository: SchematicStorageService,
    preview_publisher: SchematicPreviewStorage,
    preview_artifacts: LocalArtifactStore,
) -> None:
    digest = await repository.put_file(b"shared-preview", source_format=SchematicFormat.LITEMATIC)
    first_id = await repository.record_analysis(1, digest, make_analysis(), primary=True)
    second_id = await repository.record_analysis(2, digest, make_analysis(), primary=True)
    shared_key = "renders/shared.png"
    orphan_key = "renders/orphan.png"
    await _ready_preview_object(preview_publisher, preview_artifacts, shared_key)
    await _ready_preview_object(preview_publisher, preview_artifacts, orphan_key)
    for schematic_id, build_id in ((first_id, 1), (second_id, 2)):
        rendered = await preview_publisher.publish_fresh_preview(
            schematic_id,
            "shared-recipe",
            f"https://api.example/builds/{build_id}/shared-preview",
            shared_key,
            width=768,
            height=768,
            byte_size=42,
        )
        assert rendered is not None

    removed = await preview_publisher.cleanup_unreferenced_preview_objects(
        older_than=Instant.now().add(hours=1),
        limit=10,
    )

    assert removed == 1
    assert await preview_artifacts.stat(orphan_key) is None
    assert await preview_artifacts.stat(shared_key) is not None


async def test_missing_cached_preview_is_marked_for_safe_regeneration(
    repository: SchematicStorageService,
    preview_publisher: SchematicPreviewStorage,
    preview_artifacts: LocalArtifactStore,
) -> None:
    digest = await repository.put_file(b"missing-preview", source_format=SchematicFormat.LITEMATIC)
    schematic_id = await repository.record_analysis(1, digest, make_analysis(), primary=True)
    object_key = "renders/missing.png"
    await _ready_preview_object(preview_publisher, preview_artifacts, object_key)
    rendered = await preview_publisher.publish_fresh_preview(
        schematic_id,
        "missing-recipe",
        "https://api.example/v1/schematic-renders/missing-recipe/content",
        object_key,
        width=768,
        height=768,
        byte_size=42,
    )
    assert rendered is not None
    await preview_artifacts.delete(object_key)

    cached = await preview_publisher.get_render(schematic_id, "missing-recipe")
    reservation = await preview_publisher.reserve_preview_object(
        object_key,
        byte_size=42,
        sha256=hashlib.sha256(b"p" * 42).hexdigest(),
    )

    assert cached is None
    assert reservation.upload_required is True


async def test_cached_preview_that_disappears_during_publication_is_retried(
    repository: SchematicStorageService,
    preview_artifacts: LocalArtifactStore,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class DisappearingArtifactStore(LocalArtifactStore):
        def __init__(self, delegate: LocalArtifactStore) -> None:
            self._delegate = delegate
            self._stat_calls = 0

        @override
        async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
            return await self._delegate.put(key, data, content_type=content_type)

        @override
        async def stat(self, key: str) -> ArtifactMetadata | None:
            self._stat_calls += 1
            if self._stat_calls == 2:
                await self._delegate.delete(key)
            return await self._delegate.stat(key)

        @override
        async def delete(self, key: str) -> None:
            await self._delegate.delete(key)

    artifacts = DisappearingArtifactStore(preview_artifacts)
    publisher = SchematicPreviewStorage(artifacts, uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))
    digest = await repository.put_file(b"vanishing-preview", source_format=SchematicFormat.LITEMATIC)
    schematic_id = await repository.record_analysis(1, digest, make_analysis(), primary=True)
    object_key = "renders/vanishing.png"
    await _ready_preview_object(publisher, artifacts, object_key)
    url = "https://api.example/v1/schematic-renders/vanishing/content"
    rendered = await publisher.publish_fresh_preview(
        schematic_id,
        "vanishing-recipe",
        url,
        object_key,
        width=768,
        height=768,
        byte_size=42,
    )
    assert rendered is not None
    assert await publisher.get_render(schematic_id, "vanishing-recipe") is not None

    with pytest.raises(DataIntegrityError, match="disappeared"):
        await publisher.publish_cached_preview(schematic_id, "vanishing-recipe", url)

    reservation = await publisher.reserve_preview_object(
        object_key,
        byte_size=42,
        sha256=hashlib.sha256(b"p" * 42).hexdigest(),
    )
    assert reservation.upload_required is True


async def test_a_fingerprint_lookup_finds_the_same_build_resubmitted_under_another_id(
    repository: SchematicStorageService,
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
    repository: SchematicStorageService,
) -> None:
    digest = await repository.put_file(b"shared", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, digest, make_analysis(), primary=True)
    await repository.record_analysis(2, digest, make_analysis(), primary=True)

    matches = await repository.find_file_matches(digest, exclude_build_id=2)

    assert [match.build_id for match in matches] == [1]


async def test_a_fingerprint_from_another_engine_version_never_matches(
    repository: SchematicStorageService,
) -> None:
    """Fingerprint definitions can change between engine releases, so lookups must include the version."""
    digest = await repository.put_file(b"original", source_format=SchematicFormat.LITEMATIC)
    await repository.record_analysis(1, digest, make_analysis(shape="s", analyzer_version="engine-1"), primary=True)

    assert (
        await repository.find_fingerprint_matches("s", preset=FingerprintPreset.SHAPE, analyzer_version="engine-2")
        == []
    )


async def test_metric_neighbours_shortlist_builds_of_comparable_size(
    repository: SchematicStorageService,
) -> None:
    close = make_analysis(dimensions=(3, 4, 5), block_count=50)
    far = make_analysis(dimensions=(80, 90, 100), block_count=100_000)
    for build_id, payload, analysis in ((1, b"close", close), (2, b"far", far)):
        digest = await repository.put_file(payload, source_format=SchematicFormat.LITEMATIC)
        await repository.record_analysis(build_id, digest, analysis, primary=True)

    neighbours = await repository.find_metric_neighbours(close.metrics, tolerance=0.2, exclude_build_id=99)

    assert [neighbour.build_id for neighbour in neighbours] == [1]


async def test_the_stored_read_model_carries_the_file_facts_from_the_file_row(
    repository: SchematicStorageService,
) -> None:
    """Read format and byte size from the file metadata, even when the analysis reports different values."""
    digest = await repository.put_file(b"sponge-bytes", source_format=SchematicFormat.SPONGE_SCHEM)
    await repository.record_analysis(1, digest, make_analysis(), primary=True)

    stored = (await repository.list_for_build(1))[0]

    assert stored.analysis.metrics.source_format is SchematicFormat.SPONGE_SCHEM
    assert stored.analysis.metrics.byte_size == len(b"sponge-bytes")


async def test_publication_round_trips_and_reanalysis_does_not_reset_it(
    repository: SchematicStorageService,
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
        published_at=now,
    )
    schematic_id = await repository.record_analysis(
        1,
        digest,
        make_analysis(),
        primary=True,
        publication=publication,
    )

    await repository.record_analysis(1, digest, make_analysis(block_count=59), primary=True)
    stored = await repository.get_for_build(1, schematic_id)

    assert stored is not None
    assert stored.publication == publication
    assert stored.analysis.metrics.block_count == 59


async def test_schema_ceiling_accepts_the_exact_boundary_and_refuses_one_byte_more(
    repository: SchematicStorageService,
) -> None:
    """Defence in depth: the size cap is a check constraint, not only an application rule."""
    exact = b"\x00" * SCHEMATIC_FILE_SCHEMA_MAX_BYTES
    digest = await repository.put_file(exact, source_format=SchematicFormat.LITEMATIC)
    assert await repository.get_file(digest) == exact

    with pytest.raises(IntegrityError):
        await repository.put_file(
            b"\x00" * (SCHEMATIC_FILE_SCHEMA_MAX_BYTES + 1),
            source_format=SchematicFormat.LITEMATIC,
        )


async def test_simulation_evidence_round_trips_without_changing_the_analysis(
    repository: SchematicStorageService,
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


async def test_preview_cleanup_commits_its_fence_before_deleting_storage(
    schematic_tables: AsyncEngine,
    async_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    started, release = anyio.Event(), anyio.Event()

    class PausedDeletion(LocalArtifactStore):
        @override
        async def delete(self, key: str) -> None:
            started.set()
            await release.wait()
            await super().delete(key)

    artifacts = PausedDeletion(tmp_path / "cleanup")
    publisher = SchematicPreviewStorage(artifacts, uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))
    key = "renders/cleanup.png"
    await _ready_preview_object(publisher, artifacts, key)

    async def cleanup() -> None:
        assert await publisher.cleanup_unreferenced_preview_objects(older_than=Instant.now().add(hours=1), limit=1) == 1

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(cleanup)
            await started.wait()
            with pytest.raises(PreviewCleanupInProgressError):
                await publisher.reserve_preview_object(key, byte_size=42, sha256=hashlib.sha256(b"p" * 42).hexdigest())
            release.set()
    assert await artifacts.get(key, max_bytes=42) is None
    replacement = await publisher.reserve_preview_object(
        key, byte_size=42, sha256=hashlib.sha256(b"p" * 42).hexdigest()
    )
    assert replacement.upload_required
