"""Schematic application service tests."""

import gzip
import logging
import zlib

import pytest

from squid.schematics.application import ConvertRequest, IngestRequest, SchematicService
from squid.schematics.domain.models import (
    AutostackLattice,
    FingerprintPreset,
    SchematicComparison,
    SchematicFormat,
    SchematicLimits,
)
from squid.schematics.errors import (
    DecompressionBudgetExceededError,
    InvalidSchematicError,
    SchematicNotFoundError,
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


def service(
    analyzer: FakeSchematicAnalyzer | None = None,
    store: FakeSchematicStore | None = None,
    *,
    limits: SchematicLimits | None = None,
    engine_installed: bool = True,
    render_enabled: bool = False,
    resource_pack: "FakeResourcePack | None" = None,
    render_max_block_count: int = 400_000,
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
        ),
        analyzer,
        store,
    )


async def test_ingest_stores_bytes_and_passes_the_sniffed_format_to_the_analyzer() -> None:
    schematics, analyzer, store = service()
    data = litematic_bytes()

    ingested = await schematics.ingest(IngestRequest(data=data, filename="door.litematic"))

    assert store.files[ingested.sha256] == data
    assert analyzer.analyze_calls == [(data, SchematicFormat.LITEMATIC, True)]


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
    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="door.litematic"))

    prepared = await schematics.prepare_render(7)

    assert prepared is not None
    assert prepared.png == analyzer.render_output
    assert analyzer.render_calls[0][2] == b"resource-pack"
    await schematics.record_render(prepared, "https://cdn.example/render.png")

    cached = await schematics.prepare_render(7)
    assert cached is not None
    assert cached.cached_url == "https://cdn.example/render.png"
    assert len(analyzer.render_calls) == 1


async def test_render_skips_a_schematic_over_the_block_cap(caplog: pytest.LogCaptureFixture) -> None:
    analyzer = FakeSchematicAnalyzer(make_analysis(block_count=401))
    schematics, _, _ = service(
        analyzer,
        render_enabled=True,
        resource_pack=FakeResourcePack(),
        render_max_block_count=400,
    )
    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="door.litematic"))

    with caplog.at_level(logging.INFO, logger="squid.schematics.application.services"):
        assert await schematics.prepare_render(7) is None

    assert analyzer.render_calls == []
    assert vars(caplog.records[-1])["squid.build.id"] == 7
    assert vars(caplog.records[-1])["squid.schematic.format"] == "litematic"
    assert vars(caplog.records[-1])["squid.schematic.operation"] == "render"


async def test_render_worker_crash_is_not_retried() -> None:
    analyzer = FakeSchematicAnalyzer()
    schematics, _, store = service(analyzer, render_enabled=True, resource_pack=FakeResourcePack())
    await schematics.attach(7, IngestRequest(data=litematic_bytes(), filename="door.litematic"))
    analyzer.failure = SchematicWorkerCrashedError(operation="render", exit_code=-9)

    assert await schematics.prepare_render(7) is None
    assert await schematics.prepare_render(7) is None
    await store.record_analysis(8, store.records[0][1], analyzer.analysis, primary=True)
    assert await schematics.prepare_render(8) is None
    assert len(analyzer.render_calls) == 1


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
