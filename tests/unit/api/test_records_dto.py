"""Published records serialize through Litestar without an intermediate API model."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec
from uuid import UUID

import httpx
import pytest
from litestar import Litestar, get, post
from litestar.di import Provide

from squid.api.security import ANONYMOUS
from squid.api.v1.records import (
    PublicRecordDTO,
    get_door_ranges,
    get_record,
    list_records,
    lookup_door_ranges,
)
from squid.core.builds.application import PublicBuildDimensions, PublicBuildPreview, PublicBuildSummary, PublicBuildTag
from squid.core.builds.application.queries import PublicBuildCreator, PublicBuildStatus
from squid.core.builds.domain import BuildCategory
from squid.core.builds.domain.expansion import DoorExpansion, ExpansionBranch
from squid.core.builds.domain.expansion_evaluation import PointStatus
from squid.core.pagination import Page, PageAnchor
from squid.core.records.application import PublicRecordQueryService, RecordService
from squid.core.records.application.expansion_regions import DoorRangeQuery, DoorRangeResult, analyze_door_widths
from squid.core.records.application.live import LiveDoorConfiguration, LiveDoorRecord
from squid.core.records.application.models import PublicRecordDetail, PublishedRecordResult
from squid.core.records.application.range_publication import RangePublicationService
from squid.core.records.application.range_snapshots import RangeAttempt, RangePublication
from squid.core.records.domain import BuildKind, RecordCriterion, RecordResolution, VersionScope
from squid.core.records.domain.door_evidence import DoorExpansionSource, DoorRecordEvidence
from squid.core.records.domain.regions import RegionAnalysisStatus, SmallestRegionAnalysis

_RECORD = PublishedRecordResult(
    result_id=7,
    definition_id=3,
    record_id=UUID("22222222-2222-2222-2222-222222222222"),
    title="Fastest 2x3 door",
    subtitle="All versions",
    criterion=RecordCriterion.FASTEST,
    build_kind=BuildKind.DOOR,
    version_scope=VersionScope.ALL_TIME,
    holder_build_ids=(42,),
    computed_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
    last_checked_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
    refresh_pending=True,
)
_RECORD_JSON = {
    "result_id": 7,
    "definition_id": 3,
    "record_id": "22222222-2222-2222-2222-222222222222",
    "title": "Fastest 2x3 door",
    "subtitle": "All versions",
    "criterion": RecordCriterion.FASTEST.value,
    "build_kind": BuildKind.DOOR.value,
    "version_scope": VersionScope.ALL_TIME.value,
    "holder_build_ids": [42],
    "tied_build_ids": [],
    "computed_at": "2026-08-10T12:00:00Z",
    "last_checked_at": "2026-08-11T12:00:00Z",
    "refresh_pending": True,
    "version_id": None,
}


async def test_record_listing_dto_preserves_json_and_declares_public_fields() -> None:
    record = _RECORD
    records = create_autospec(RecordService, instance=True)
    page = Page(items=(record,), total=2, next=PageAnchor(after_id=7), prev=None)
    records.list_page.return_value = page
    handler = get(
        "/records",
        return_dto=PublicRecordDTO,
        dependencies={"records": Provide(lambda: records, sync_to_thread=False)},
    )(list_records.fn)
    app = Litestar([handler], dependencies={"caller": Provide(lambda: ANONYMOUS, sync_to_thread=False)})

    # Litestar annotates ASGI scopes more narrowly than httpx's mutable dictionaries.
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/records")

    assert response.status_code == 200
    assert response.json() == {
        "items": [_RECORD_JSON],
        "total": 2,
        "next": {"offset": None, "after_id": 7, "before_id": None},
        "prev": None,
    }
    # The HTTP wrapper may be transformed by the DTO, but the service's frozen page stays intact.
    assert page.items == (record,)
    schemas = app.openapi_schema.to_schema()["components"]["schemas"]
    public = schemas["PublishedRecordResult"]
    assert set(public["properties"]) == PublicRecordDTO.config.include
    assert public["properties"]["holder_build_ids"]["type"] == "array"
    assert public["properties"]["computed_at"]["format"] == "date-time"
    assert public["properties"]["record_id"]["format"] == "uuid"


@pytest.mark.parametrize("expanded", [False, True])
async def test_record_detail_serializes_the_application_read_model(expanded: bool) -> None:
    holder = PublicBuildSummary(
        id=42,
        revision=3,
        title="Fastest 2x3 door",
        display_name=None,
        status=PublicBuildStatus.APPROVED,
        category=BuildCategory.DOOR,
        dimensions=PublicBuildDimensions(width=5, height=6, depth=None),
        creators=(PublicBuildCreator("Alice", None),),
        tags=(PublicBuildTag(key="official_delay", name="Delay", value=Decimal("1.5"), unit="second"),),
        preview=PublicBuildPreview(kind="render", url="https://media.example/render.png"),
        version_spec=">=1.21",
        versions=("Java 1.21.5",),
        opening_time=8,
        closing_time=None,
        created_at=datetime(2026, 8, 1, 9, 30, tzinfo=UTC),
        updated_at=None,
    )
    records = create_autospec(PublicRecordQueryService, instance=True)
    evidence = (
        DoorRecordEvidence(
            evaluation=LiveDoorRecord(
                opening=(2, 3, 1),
                criterion=RecordCriterion.FASTEST,
                version_id=None,
                evaluated_at=_RECORD.computed_at,
                resolution=RecordResolution(holder_ids=(42,)),
                holder_configuration_ids=(0,),
                configurations=(
                    LiveDoorConfiguration(
                        id=0,
                        build_id=42,
                        branch_index=0,
                        membership=PointStatus.SUPPORTED,
                        parameters=(("n", 2),),
                        volume=10,
                    ),
                ),
            ),
            sources=(
                DoorExpansionSource(
                    build_id=42,
                    expansion=DoorExpansion(
                        branches=(ExpansionBranch(volume="5n"),),
                    ),
                ),
            ),
        )
        if expanded
        else None
    )
    records.get.return_value = PublicRecordDetail.with_builds(
        replace(_RECORD, door_evidence=evidence), {holder.id: holder}
    )
    handler = get(
        "/records/{record_id:uuid}",
        dependencies={
            "records": Provide(lambda: records, sync_to_thread=False),
            "services": Provide(lambda: None, sync_to_thread=False),
        },
    )(get_record.fn)
    app = Litestar([handler], dependencies={"caller": Provide(lambda: ANONYMOUS, sync_to_thread=False)})

    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(f"/records/{_RECORD.record_id}")

    assert response.status_code == 200
    assert response.json() == {
        **_RECORD_JSON,
        "door_evidence": evidence.model_dump(mode="json") if evidence is not None else None,
        "viewer": None,
        "gaps": [],
        "tied_builds": [],
        "contender_builds": [],
        "history": [],
        "retired_at": None,
        "holder_builds": [
            {
                "id": 42,
                "revision": 3,
                "title": "Fastest 2x3 door",
                "display_name": None,
                "status": "approved",
                "category": "Door",
                "dimensions": {"width": 5, "height": 6, "depth": None},
                "creators": [{"name": "Alice", "account_id": None}],
                "record_summary": {"held_count": 0, "former_count": 0},
                "credits": [],
                "subtitle": None,
                # Decimal tag values travel as strings so clients keep their exact precision.
                "tags": [{"key": "official_delay", "name": "Delay", "value": "1.5", "unit": "second"}],
                "preview": {"kind": "render", "url": "https://media.example/render.png", "width": None, "height": None},
                "version_spec": ">=1.21",
                "versions": ["Java 1.21.5"],
                "opening_time": 8,
                "closing_time": None,
                "created_at": "2026-08-01T09:30:00Z",
                "updated_at": None,
            }
        ],
    }
    schemas = app.openapi_schema.to_schema()["components"]["schemas"]
    assert schemas["PublicRecordDetail"]["properties"]["holder_builds"]["items"] == {
        "$ref": "#/components/schemas/PublicBuildSummary"
    }


@pytest.mark.parametrize("status", [RegionAnalysisStatus.EXACT, RegionAnalysisStatus.TIMED_OUT])
async def test_range_lookup_serializes_live_analysis_and_validates_dimensions(status: RegionAnalysisStatus) -> None:
    records = create_autospec(RecordService, instance=True)
    analysis = analyze_door_widths((), height=3).analysis
    if status is RegionAnalysisStatus.TIMED_OUT:
        analysis = SmallestRegionAnalysis(status, diagnostic="Elapsed-time limit exceeded.")
    query = DoorRangeQuery(height=3)
    records.lookup_door_ranges.return_value = DoorRangeResult(
        query, datetime(2026, 8, 10, 12, tzinfo=UTC), analysis, ()
    )
    handler = post(
        "/records/lookup/door/ranges",
        status_code=200,
        dependencies={"records": Provide(lambda: records, sync_to_thread=False)},
    )(lookup_door_ranges.fn)
    app = Litestar([handler], dependencies={"caller": Provide(lambda: ANONYMOUS, sync_to_thread=False)})
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.post("/records/lookup/door/ranges", json={"height": 3})
        assert response.status_code == 200
        body = response.json()
        assert body["query"]["axis"] == "width"
        assert body["query"]["maximum"] is None
        assert body["analysis"]["status"] == status.value
        if status is RegionAnalysisStatus.TIMED_OUT:
            assert body["analysis"]["regions"] == []
        else:
            assert body["analysis"]["regions"][0]["domain"]["maximum"] is None
        records.lookup_door_ranges.assert_awaited_once_with(query, context=ANONYMOUS.context())
        records.lookup_or_materialize.assert_not_called()
        records.lookup_door_ranges.reset_mock()
        for invalid in (
            {"height": 0},
            {"height": True},
            {"height": 1.5},
            {"height": 3, "depth": False},
            {"height": 3, "minimum": "1"},
            {"height": 3, "maximum": 1.5},
            {"height": 3, "minimum": 5, "maximum": 4},
            {"height": 3, "axis": "height"},
            {"height": 3, "criterion": "fastest"},
            {"height": 3, "unexpected": True},
        ):
            response = await client.post("/records/lookup/door/ranges", json=invalid)
            assert response.status_code == 400, invalid
        records.lookup_door_ranges.assert_not_called()
    schemas = app.openapi_schema.to_schema()["components"]["schemas"]
    assert "DoorRangeQuery" in schemas
    assert "DoorRangeResult" in schemas


async def test_published_ranges_show_active_and_failed_latest_attempts() -> None:
    publications = create_autospec(RangePublicationService, instance=True)
    moment = datetime(2026, 11, 14, tzinfo=UTC)
    active = RangeAttempt(
        1,
        7,
        1,
        3,
        moment,
        moment,
        is_active=True,
        result=analyze_door_widths((), height=3),
        error=None,
        snapshot_hash="abc",
        captured_at=moment,
    )
    latest = RangeAttempt(
        2,
        7,
        2,
        3,
        moment,
        moment,
        is_active=False,
        result=None,
        error="cancelled",
        snapshot_hash="def",
        captured_at=moment,
    )
    publications.get.return_value = RangePublication(7, DoorRangeQuery(height=3), active, latest)
    handler = get(
        "/records/ranges/door/{query_id:int}",
        dependencies={"publications": Provide(lambda: publications, sync_to_thread=False)},
    )(get_door_ranges.fn)
    app = Litestar([handler], dependencies={"caller": Provide(lambda: ANONYMOUS, sync_to_thread=False)})
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/records/ranges/door/7")
        assert response.status_code == 200
        body = response.json()
        assert body["query_id"] == 7
        assert body["active"]["id"] == 1
        assert body["active"]["result"]["analysis"]["status"] == "exact"
        assert body["latest"]["error"] == "cancelled"
        assert body["latest"]["snapshot_hash"] == "def"
        assert body["latest"]["result"] is None
        publications.get.assert_awaited_once_with(7, context=ANONYMOUS.context())
        publications.publish.assert_not_called()
        publications.register.assert_not_called()
