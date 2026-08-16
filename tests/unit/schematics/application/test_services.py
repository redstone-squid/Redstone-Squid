"""Schematic application service tests."""

import gzip
import logging
import zlib

import pytest
from whenever import Instant

from squid.schematics.application import (
    CachedRender,
    ConvertRequest,
    FreshRender,
    IngestRequest,
    RenderSkipReason,
    SchematicPublication,
    SchematicService,
    SkippedRender,
)
from squid.schematics.domain.models import (
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicFormat,
    SchematicLicense,
    SchematicLimits,
    SchematicVisibility,
)
from squid.schematics.errors import (
    DecompressionBudgetExceededError,
    InvalidSchematicError,
    SchematicNotFoundError,
    SchematicRenderUnavailableError,
    SchematicSupportUnavailableError,
    SchematicTooLargeError,
    SchematicWorkerCrashedError,
)
from tests.unit.schematics.fakes import (
    FakeSchematicAnalyzer,
    FakeSchematicStore,
    FakeVersionResolver,
    make_analysis,
)


def litematic_bytes(payload: bytes = b"") -> bytes:
    """Return gzipped NBT whose root compound names the litematic marker members."""
    body = (
        b"\x0a\x00\x00"
        + b"\x0a"
        + len(b"Metadata").to_bytes(2, "big")
        + b"Metadata"
        + b"\x0a"
        + len(b"Regions").to_bytes(2, "big")
        + b"Regions"
        + payload
    )
    return gzip.compress(body)


class FakeResourcePack:
    """Return deterministic operator-owned pack bytes without filesystem I/O."""

    async def load(self) -> tuple[bytes, str]:
        return b"resource-pack", "a" * 64

    async def aclose(self) -> None:
        """Release no resources in the in-memory provider."""


def service(
    analyzer: FakeSchematicAnalyzer | None = None,
    store: FakeSchematicStore | None = None,
    *,
    limits: SchematicLimits | None = None,
    engine_installed: bool = True,
    render_enabled: bool = False,
    resource_pack: FakeResourcePack | None = None,
    render_max_block_count: int = 400_000,
    render_max_bounding_volume: int = 2_000_000,
) -> tuple[SchematicService, FakeSchematicAnalyzer, FakeSchematicStore]:
    analyzer = analyzer or FakeSchematicAnalyzer()
    store = store or FakeSchematicStore()
    return (
        SchematicService(
            analyzer,
            store,
            FakeVersionResolver(),
            limits=limits,
            engine_installed=engine_installed,
            render_enabled=render_enabled,
            resource_pack=resource_pack,
            render_max_block_count=render_max_block_count,
            render_max_bounding_volume=render_max_bounding_volume,
        ),
        analyzer,
        store,
    )


def sanitized_publication(*, public: bool = False) -> SchematicPublication:
    now = Instant.parse_iso("2026-08-11T12:00:00Z")
    if public:
        return SchematicPublication(
            visibility=SchematicVisibility.PUBLIC_DOWNLOAD,
            license=SchematicLicense.CC_BY_4_0,
            rights_attested_at=now,
            rights_attested_by_account_id=7,
            sanitized_at=now,
            sanitizer_version="nucleation-test-sanitizer",
            sanitization_report={"removed": 0},
            published_at=now,
        )
    return SchematicPublication(
        visibility=SchematicVisibility.REVIEWER_ONLY,
        sanitized_at=now,
        sanitizer_version="nucleation-test-sanitizer",
        sanitization_report={"removed": 0},
    )


def test_default_upload_budgets_match_the_public_plugin_contract() -> None:
    limits = SchematicLimits()

    assert limits.max_upload_bytes == 16 * 1024 * 1024
    assert limits.max_inflated_bytes == 64 * 1024 * 1024
    assert limits.max_allocated_volume == 20_000_000
    assert limits.max_axis_length == 512


def test_publication_requires_rights_and_a_completed_sanitizer_identity() -> None:
    now = Instant.parse_iso("2026-08-11T12:00:00Z")

    with pytest.raises(ValueError, match="license and rights"):
        SchematicPublication(visibility=SchematicVisibility.PUBLIC_DOWNLOAD)
    with pytest.raises(ValueError, match="audit report"):
        SchematicPublication(sanitizer_version="nucleation-test")
    assert sanitized_publication(public=True).is_public_downloadable is True
    assert SchematicLicense.CC_BY_NC_SA_4_0.uri == "https://creativecommons.org/licenses/by-nc-sa/4.0/"
    assert (
        SchematicPublication(
            visibility=SchematicVisibility.PUBLIC_DOWNLOAD,
            license=SchematicLicense.CC0_1_0,
            rights_attested_at=now,
            rights_attested_by_account_id=7,
        ).is_public_downloadable
        is False
    )


async def test_ingest_stores_bytes_and_passes_the_sniffed_format_to_the_analyzer() -> None:
    schematics, analyzer, store = service()
    data = litematic_bytes()

    ingested = await schematics.ingest(IngestRequest(data=data, filename="door.litematic"))

    assert store.files[ingested.sha256] == data
    assert analyzer.analyze_calls == [(data, SchematicFormat.LITEMATIC, True)]


async def test_public_content_requires_the_build_attachment_and_complete_publication() -> None:
    schematics, _analyzer, store = service()
    digest = await store.put_file(b"stored", source_format=SchematicFormat.SPONGE_SCHEM)
    public_id = await store.record_analysis(
        7,
        digest,
        make_analysis(),
        primary=True,
        publication=sanitized_publication(public=True),
    )

    download = await schematics.public_download(7, public_id)

    assert download.content == b"stored"
    # Carried on the result rather than asserted in the route: the domain already
    # guarantees a public attachment has a license.
    assert download.license is SchematicLicense.CC_BY_4_0
    assert download.source_format is make_analysis().metrics.source_format
    with pytest.raises(SchematicNotFoundError):
        await schematics.public_download(8, public_id)

    private_id = await store.record_analysis(7, digest, make_analysis(), primary=False)
    with pytest.raises(SchematicNotFoundError):
        await schematics.public_download(7, private_id)


async def test_public_listing_hides_legacy_and_withdrawn_attachments() -> None:
    schematics, _analyzer, store = service()
    digest = await store.put_file(b"stored", source_format=SchematicFormat.SPONGE_SCHEM)
    public = sanitized_publication(public=True)
    await store.record_analysis(7, digest, make_analysis(), primary=True, publication=public)
    await store.record_analysis(7, digest, make_analysis(), primary=False)
    await store.record_analysis(
        7,
        digest,
        make_analysis(),
        primary=False,
        publication=SchematicPublication(
            visibility=public.visibility,
            license=public.license,
            rights_attested_at=public.rights_attested_at,
            rights_attested_by_account_id=public.rights_attested_by_account_id,
            sanitized_at=public.sanitized_at,
            sanitizer_version=public.sanitizer_version,
            sanitization_report=public.sanitization_report,
            published_at=public.published_at,
            withdrawn_at=Instant.parse_iso("2026-08-11T13:00:00Z"),
        ),
    )

    assert [item.id for item in await schematics.list_public_for_build(7)] == [1]
    page = await schematics.list_public_page(7)
    assert [item.id for item in page.items] == [1]
    assert page.total == 1


async def test_ingest_refuses_an_oversized_upload_before_reaching_the_analyzer() -> None:
    schematics, analyzer, _ = service(limits=SchematicLimits(max_upload_bytes=8))

    with pytest.raises(SchematicTooLargeError):
        await schematics.ingest(IngestRequest(data=litematic_bytes(b"\x00" * 512), filename="door.litematic"))

    assert analyzer.analyze_calls == []


async def test_ingest_refuses_a_decompression_bomb_before_reaching_the_analyzer() -> None:
    bomb = zlib.compress(b"\x00" * (4 * 1024 * 1024))
    schematics, analyzer, _ = service(limits=SchematicLimits(max_inflated_bytes=1024))

    with pytest.raises(DecompressionBudgetExceededError):
        await schematics.ingest(IngestRequest(data=bomb, filename="door.litematic"))

    assert analyzer.analyze_calls == []


async def test_ingest_refuses_bytes_that_are_not_a_schematic_however_they_are_named() -> None:
    schematics, analyzer, _ = service()

    with pytest.raises(InvalidSchematicError):
        await schematics.ingest(IngestRequest(data=gzip.compress(b"not nbt at all"), filename="door.litematic"))

    assert analyzer.analyze_calls == []


async def test_a_file_that_crashed_a_worker_is_never_analyzed_a_second_time() -> None:
    """Retrying a payload that just killed a process is how a crash loop starts."""
    crash = SchematicWorkerCrashedError(operation="analyze", exit_code=-9)
    schematics, analyzer, _ = service(FakeSchematicAnalyzer(failure=crash))
    request = IngestRequest(data=litematic_bytes(), filename="poison.litematic")

    with pytest.raises(SchematicWorkerCrashedError):
        await schematics.ingest(request)
    with pytest.raises(InvalidSchematicError):
        await schematics.ingest(request)

    assert len(analyzer.analyze_calls) == 1


async def test_attach_records_the_analysis_against_the_build() -> None:
    schematics, _, store = service()

    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="door.litematic"))

    build_id, _, analysis, primary = store.records[0]
    assert (build_id, primary) == (7, True)
    assert analysis.fingerprints.shape == "shape-hash"


async def test_duplicate_detection_reports_a_byte_identical_file_without_comparing() -> None:
    schematics, analyzer, _ = service()
    request = IngestRequest(data=litematic_bytes(), filename="door.litematic")
    await schematics.attach(7, request)

    duplicate = await schematics.ingest(request)
    matches = await schematics.find_duplicates(duplicate)

    assert [(match.build_id, match.tier) for match in matches] == [(7, "identical")]
    assert analyzer.compare_calls == []


async def test_duplicate_detection_uses_shape_as_the_moved_or_rotated_identity() -> None:
    schematics, analyzer, _ = service()
    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="original.litematic"))

    moved = await schematics.ingest(IngestRequest(data=litematic_bytes(b"\x00"), filename="moved.litematic"))
    matches = await schematics.find_duplicates(moved)

    assert [(match.build_id, match.tier) for match in matches] == [(7, "structural-match")]
    assert analyzer.compare_calls == []


async def test_duplicate_detection_compares_only_shortlisted_near_matches() -> None:
    analyzer = FakeSchematicAnalyzer(make_analysis(shape="new-shape"))
    schematics, _, store = service(analyzer)
    candidate_data = b"candidate"
    candidate_digest = await store.put_file(candidate_data, source_format=SchematicFormat.LITEMATIC)
    await store.record_analysis(
        7,
        candidate_digest,
        make_analysis(shape="old-shape", block_count=43),
        primary=True,
    )
    analyzer.comparisons[candidate_data] = SchematicComparison(
        preset=FingerprintPreset.SHAPE,
        identical=False,
        footprint_distance=0.5,
        summary='{"changed":1}',
    )

    uploaded = await schematics.ingest(IngestRequest(data=litematic_bytes(), filename="new.litematic"))
    matches = await schematics.find_duplicates(uploaded)

    assert [(match.build_id, match.tier, match.footprint_distance) for match in matches] == [(7, "near", 0.5)]
    assert matches[0].detail == '{"changed":1}'
    assert len(analyzer.compare_calls) == 1
    assert analyzer.compare_calls[0][2] is FingerprintPreset.SHAPE
    assert analyzer.compare_calls[0][3] is not None


async def test_duplicate_detection_discards_comparisons_beyond_the_near_threshold() -> None:
    analyzer = FakeSchematicAnalyzer(make_analysis(shape="new-shape"))
    schematics, _, store = service(analyzer)
    candidate_data = b"candidate"
    candidate_digest = await store.put_file(candidate_data, source_format=SchematicFormat.LITEMATIC)
    await store.record_analysis(7, candidate_digest, make_analysis(shape="old-shape"), primary=True)
    analyzer.comparisons[candidate_data] = SchematicComparison(
        preset=FingerprintPreset.SHAPE,
        identical=False,
        footprint_distance=1.01,
    )

    uploaded = await schematics.ingest(IngestRequest(data=litematic_bytes(), filename="new.litematic"))

    assert await schematics.find_duplicates(uploaded) == []


async def test_record_attaches_an_analysis_produced_before_the_build_existed() -> None:
    """The submission flow analyzes first so it can prefill the form, and records after."""
    schematics, _, store = service()
    request = IngestRequest(data=litematic_bytes(), filename="door.litematic")
    ingested = await schematics.ingest(request)

    await schematics.record(11, ingested, request, primary=False)

    assert store.records == [(11, ingested.sha256, ingested.analysis, False)]


async def test_convert_resolves_a_version_label_to_a_data_version() -> None:
    schematics, analyzer, store = service()
    await schematics.attach(3, IngestRequest(data=litematic_bytes(), filename="door.litematic"))
    store.files[store.records[0][1]] = litematic_bytes()

    await schematics.convert(3, ConvertRequest(target_format=SchematicFormat.LITEMATIC), version_label="Java 1.16.5")

    assert analyzer.convert_calls == [(SchematicFormat.LITEMATIC, 2586)]


async def test_convert_refuses_a_version_it_has_no_data_version_for() -> None:
    schematics, _, _ = service()
    await schematics.attach(3, IngestRequest(data=litematic_bytes(), filename="door.litematic"))

    with pytest.raises(InvalidSchematicError):
        await schematics.convert(
            3, ConvertRequest(target_format=SchematicFormat.LITEMATIC), version_label="Java 1.7.10"
        )


async def test_convert_reports_a_build_with_no_schematic() -> None:
    schematics, _, _ = service()

    with pytest.raises(SchematicNotFoundError):
        await schematics.convert(404, ConvertRequest(target_format=SchematicFormat.LITEMATIC))


async def test_an_instance_without_the_engine_reports_unavailable_rather_than_failing_late() -> None:
    schematics, analyzer, _ = service(engine_installed=False)

    assert schematics.available is False
    assert (await schematics.capabilities()).available is False
    with pytest.raises(SchematicSupportUnavailableError):
        await schematics.ingest(IngestRequest(data=litematic_bytes(), filename="door.litematic"))
    assert analyzer.analyze_calls == []


async def test_attaching_a_second_schematic_can_leave_the_first_primary() -> None:
    schematics, _, store = service(FakeSchematicAnalyzer(make_analysis(shape="other")))
    await schematics.attach(5, IngestRequest(data=litematic_bytes(), filename="a.litematic"))

    await schematics.attach(5, IngestRequest(data=litematic_bytes(b"\x00"), filename="b.litematic"), primary=False)

    assert [record[3] for record in store.records] == [True, False]
    primary = await schematics.primary_for_build(5)
    assert primary is not None
    assert primary.original_filename == "a.litematic"


async def test_closing_the_service_closes_the_analyzer() -> None:
    schematics, analyzer, _ = service()

    await schematics.aclose()

    assert analyzer.closed is True


async def test_render_prepares_png_then_reuses_the_persisted_recipe() -> None:
    schematics, analyzer, _ = service(render_enabled=True, resource_pack=FakeResourcePack())
    await schematics.attach(
        7,
        IngestRequest(data=litematic_bytes(), filename="door.litematic"),
        publication=sanitized_publication(),
    )

    prepared = await schematics.prepare_render(7)

    assert isinstance(prepared, FreshRender)
    assert prepared.png == analyzer.render_output
    assert analyzer.render_calls[0][2] == b"resource-pack"
    assert await schematics.record_render(prepared, "https://cdn.example/render.png", "renders/recipe.png") is not None

    cached = await schematics.prepare_render(7)
    assert isinstance(cached, CachedRender)
    assert cached.url == "https://cdn.example/render.png"
    assert len(analyzer.render_calls) == 1


async def test_render_names_the_reason_a_build_is_skipped() -> None:
    """The durable worker and moderator surfaces both need the reason, not just "nothing"."""
    schematics, analyzer, _ = service(render_enabled=True, resource_pack=FakeResourcePack())
    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="legacy.litematic"))

    assert await schematics.prepare_render(404) == SkippedRender(RenderSkipReason.NO_PRIMARY_SCHEMATIC)
    assert await schematics.prepare_render(7) == SkippedRender(RenderSkipReason.NOT_SANITIZED)
    assert analyzer.render_calls == []


async def test_render_is_skipped_wholesale_when_previews_are_disabled() -> None:
    schematics, analyzer, _ = service(resource_pack=FakeResourcePack())
    await schematics.attach(
        7,
        IngestRequest(data=litematic_bytes(), filename="door.litematic"),
        publication=sanitized_publication(),
    )

    assert await schematics.prepare_render(7) == SkippedRender(RenderSkipReason.RENDERING_DISABLED)
    assert analyzer.render_calls == []


async def test_render_is_skipped_when_the_stored_file_has_gone_missing() -> None:
    schematics, analyzer, store = service(render_enabled=True, resource_pack=FakeResourcePack())
    await schematics.attach(
        7,
        IngestRequest(data=litematic_bytes(), filename="door.litematic"),
        publication=sanitized_publication(),
    )
    store.files.clear()

    assert await schematics.prepare_render(7) == SkippedRender(RenderSkipReason.MISSING_FILE)
    assert analyzer.render_calls == []


async def test_render_recipe_includes_the_schematic_content_identity() -> None:
    schematics, _, _ = service(render_enabled=True, resource_pack=FakeResourcePack())
    await schematics.attach(
        7,
        IngestRequest(data=litematic_bytes(), filename="first.litematic"),
        publication=sanitized_publication(),
    )
    await schematics.attach(
        8,
        IngestRequest(data=litematic_bytes(b"\x00"), filename="second.litematic"),
        publication=sanitized_publication(),
    )

    first = await schematics.prepare_render(7)
    second = await schematics.prepare_render(8)

    assert isinstance(first, FreshRender)
    assert isinstance(second, FreshRender)
    assert first.recipe_hash != second.recipe_hash


@pytest.mark.parametrize(
    ("analysis", "reason"),
    [
        (make_analysis(block_count=401), RenderSkipReason.OVER_BLOCK_BUDGET),
        (make_analysis(dimensions=(10, 10, 11)), RenderSkipReason.OVER_VOLUME_BUDGET),
    ],
)
async def test_render_skips_a_schematic_over_a_budget(
    analysis: SchematicAnalysis, reason: RenderSkipReason, caplog: pytest.LogCaptureFixture
) -> None:
    analyzer = FakeSchematicAnalyzer(analysis)
    schematics, _, _ = service(
        analyzer,
        render_enabled=True,
        resource_pack=FakeResourcePack(),
        render_max_block_count=400,
        render_max_bounding_volume=1000,
    )
    await schematics.attach(
        7,
        IngestRequest(data=litematic_bytes(), filename="door.litematic"),
        publication=sanitized_publication(),
    )

    with caplog.at_level(logging.INFO, logger="squid.schematics.application.services"):
        assert await schematics.prepare_render(7) == SkippedRender(reason)

    assert analyzer.render_calls == []
    assert vars(caplog.records[-1])["squid.build.id"] == 7
    assert vars(caplog.records[-1])["squid.schematic.format"] == "litematic"
    assert vars(caplog.records[-1])["squid.schematic.operation"] == "render"
    assert vars(caplog.records[-1])["squid.schematic.render_skip_reason"] == reason.value


async def test_render_worker_crash_is_retried_by_the_durable_queue() -> None:
    analyzer = FakeSchematicAnalyzer()
    schematics, _, store = service(analyzer, render_enabled=True, resource_pack=FakeResourcePack())
    await schematics.attach(
        7,
        IngestRequest(data=litematic_bytes(), filename="door.litematic"),
        publication=sanitized_publication(),
    )
    analyzer.failure = SchematicWorkerCrashedError(operation="render", exit_code=-9)

    with pytest.raises(SchematicWorkerCrashedError):
        await schematics.prepare_render(7)
    assert await schematics.prepare_render(7) == SkippedRender(RenderSkipReason.POISONED_FILE)
    await store.record_analysis(
        8,
        store.records[0][1],
        analyzer.analysis,
        primary=True,
        publication=sanitized_publication(),
    )
    assert await schematics.prepare_render(8) == SkippedRender(RenderSkipReason.POISONED_FILE)
    assert len(analyzer.render_calls) == 1


async def test_a_renderer_that_does_not_return_a_png_is_an_operational_failure() -> None:
    """A non-PNG payload is a broken renderer, not a build the queue should give up on."""
    analyzer = FakeSchematicAnalyzer()
    analyzer.render_output = b"<html>not a png</html>"
    schematics, _, _ = service(analyzer, render_enabled=True, resource_pack=FakeResourcePack())
    await schematics.attach(
        7,
        IngestRequest(data=litematic_bytes(), filename="door.litematic"),
        publication=sanitized_publication(),
    )

    with pytest.raises(SchematicRenderUnavailableError):
        await schematics.prepare_render(7)


async def test_measure_timing_persists_evidence_without_editing_a_build() -> None:
    schematics, analyzer, store = service()
    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="door.litematic"))

    result = await schematics.measure_timing(7, input_position=(12, 5, -3))

    assert result is analyzer.simulation_output
    assert analyzer.simulate_calls[0][1].input_position == (12, 5, -3)
    assert store.simulations[1] is result


async def test_detect_lattice_returns_the_persisted_highest_coverage_candidate() -> None:
    lattice = AutostackLattice(
        mode="1d",
        vectors=((0, 3, 0),),
        coverage=0.97,
        cell_min=(0, 0, 0),
        cell_max=(2, 2, 6),
        region_min=(0, 0, 0),
        region_max=(2, 11, 6),
    )
    schematics, _, _ = service(FakeSchematicAnalyzer(make_analysis(lattice=lattice)))
    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="door.litematic"))

    assert await schematics.detect_lattice(7) == lattice
