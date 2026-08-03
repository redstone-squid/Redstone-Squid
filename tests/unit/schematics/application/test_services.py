"""Schematic application service tests."""

import gzip
import zlib

import pytest

from squid.schematics.application import ConvertRequest, IngestRequest, SchematicService
from squid.schematics.domain.models import SchematicFormat, SchematicLimits
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


def service(
    analyzer: FakeSchematicAnalyzer | None = None,
    store: FakeSchematicStore | None = None,
    *,
    limits: SchematicLimits | None = None,
    engine_installed: bool = True,
) -> tuple[SchematicService, FakeSchematicAnalyzer, FakeSchematicStore]:
    analyzer = analyzer or FakeSchematicAnalyzer()
    store = store or FakeSchematicStore()
    return (
        SchematicService(analyzer, store, FakeVersionResolver(), limits=limits, engine_installed=engine_installed),
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
