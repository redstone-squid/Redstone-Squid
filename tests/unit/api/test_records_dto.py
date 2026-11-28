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
    get_door_ranges,
    get_record,
    list_door_ranges,
    list_records,
    lookup_door_ranges,
)
from squid.core.builds.application import PublicBuildDimensions, PublicBuildPreview, PublicBuildSummary, PublicBuildTag
from squid.core.builds.application.queries import PublicBuildCreator, PublicBuildStatus
from squid.core.builds.domain import BuildCategory
from squid.core.builds.domain.expansion import DoorExpansion, ExpansionBranch
from squid.core.builds.domain.expansion_evaluation import PointStatus
from squid.core.pagination import Page, PageAnchor, PageCursor
from squid.core.records.application import PublicRecordQueryService
from squid.core.records.application.expansion_regions import (
    DoorRangeQuery,
    DoorRangeResult,
    RangeLabels,
    RangeTag,
    analyze_door_widths,
)
from squid.core.records.application.live import LiveDoorConfiguration, LiveDoorRecord
from squid.core.records.application.metrics import MetricUnit, RecordMetric
from squid.core.records.application.models import (
    PublicRecordDetail,
    PublicRecordSummary,
    PublishedRecordResult,
    RecordListFilters,
    RecordListSort,
    RecordStatus,
)
from squid.core.records.application.range_ports import RangeListFilters
from squid.core.records.application.range_publication import RangePublicationService
from squid.core.records.application.range_snapshots import RangeAttempt, RangePublication, RangeRegistration
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
    category_key="door:door|2x3|t[]|Door:r[]:p[]",
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
    "category_key": "door:door|2x3|t[]|Door:r[]:p[]",
    "holder_build_ids": [42],
    "tied_build_ids": [],
    "computed_at": "2026-08-10T12:00:00Z",
    "last_checked_at": "2026-08-11T12:00:00Z",
    "refresh_pending": True,
    "version_id": None,
    "metrics": {},
    "holder_since": None,
    "status": "resolved",
}


async def test_record_listing_serializes_holders_and_metrics_and_passes_filters() -> None:
    record = PublicRecordSummary.with_builds(
        replace(
            _RECORD,
            metrics={"opening_time": RecordMetric.render(8, MetricUnit.GAME_TICKS)},
            holder_since=datetime(2026, 8, 1, tzinfo=UTC),
        ),
        {42: _holder()},
    )
    records = create_autospec(PublicRecordQueryService, instance=True)
    records.list_page.return_value = Page(
        items=(record,), total=2, next=PageAnchor(cursor=PageCursor("title", "x", 7)), prev=None
    )
    handler = get("/records", dependencies={"records": Provide(lambda: records, sync_to_thread=False)})(list_records.fn)
    app = Litestar([handler], dependencies={"caller": Provide(lambda: ANONYMOUS, sync_to_thread=False)})

    # Litestar annotates ASGI scopes more narrowly than httpx's mutable dictionaries.
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(
            "/records",
            params={"build_kind": "door", "status": "unresolved", "tag_slug": ["flush", "flush"], "sort": "title"},
        )

    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["holders"] == [
        {
            "build_id": 42,
            "title": "Fastest 2x3 door",
            "creators": [{"name": "Alice", "account_id": None}],
            "preview": {"kind": "render", "url": "https://media.example/render.png", "width": None, "height": None},
        }
    ]
    assert item["metrics"] == {"opening_time": {"value": 8, "unit": "game_ticks", "display": "8 gt"}}
    assert item["holder_since"] == "2026-08-01T00:00:00Z"
    assert item["category_key"] == "door:door|2x3|t[]|Door:r[]:p[]"
    assert item["status"] == "resolved"
    kwargs = records.list_page.call_args.kwargs
    assert kwargs["sort"] is RecordListSort.TITLE
    assert kwargs["filters"] == RecordListFilters(
        build_kinds=(BuildKind.DOOR,), statuses=(RecordStatus.UNRESOLVED,), tag_slugs=("flush",)
    )


def _holder() -> PublicBuildSummary:
    return PublicBuildSummary(
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
        "siblings": [],
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
    publications = create_autospec(RangePublicationService, instance=True)
    analysis = analyze_door_widths((), height=3).analysis
    if status is RegionAnalysisStatus.TIMED_OUT:
        analysis = SmallestRegionAnalysis(status, diagnostic="Elapsed-time limit exceeded.")
    query = DoorRangeQuery(height=3)
    publications.lookup.return_value = DoorRangeResult(
        query,
        datetime(2026, 8, 10, 12, tzinfo=UTC),
        analysis,
        (),
        labels=RangeLabels(restrictions=(RangeTag(7, "Width", "width", "at_most", "4"),), version="Java 1.21.5"),
    )
    handler = post(
        "/records/lookup/door/ranges",
        status_code=200,
        dependencies={"publications": Provide(lambda: publications, sync_to_thread=False)},
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
        assert body["labels"] == {
            "types": [],
            "restrictions": [{"id": 7, "name": "Width", "slug": "width", "operator": "at_most", "value": "4"}],
            "version": "Java 1.21.5",
        }
        assert body["holders"] == []
        publications.lookup.assert_awaited_once_with(query, context=ANONYMOUS.context())
        publications.lookup.reset_mock()
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
        publications.lookup.assert_not_called()
    schemas = app.openapi_schema.to_schema()["components"]["schemas"]
    assert "DoorRangeQuery" in schemas
    assert "DoorRangeResult" in schemas


async def test_registered_ranges_list_filters_and_names_their_tags() -> None:
    publications = create_autospec(RangePublicationService, instance=True)
    registration = RangeRegistration(
        7,
        DoorRangeQuery(height=3, type_ids=frozenset({20})),
        published_at=datetime(2026, 11, 14, tzinfo=UTC),
        last_checked_at=None,
        refresh_pending=False,
        labels=RangeLabels(types=(RangeTag(20, "Flush", "flush"),)),
    )
    publications.list_page.return_value = Page(items=(registration,), total=1, next=None, prev=None)
    handler = get(
        "/records/ranges/door",
        dependencies={"publications": Provide(lambda: publications, sync_to_thread=False)},
    )(list_door_ranges.fn)
    app = Litestar([handler], dependencies={"caller": Provide(lambda: ANONYMOUS, sync_to_thread=False)})
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/records/ranges/door", params={"orientation": "Door", "height": 3})
        assert response.status_code == 200
        (item,) = response.json()["items"]
        assert item["query_id"] == 7
        assert item["labels"]["types"] == [
            {"id": 20, "name": "Flush", "slug": "flush", "operator": None, "value": None}
        ]
        assert item["published_at"] == "2026-11-14T00:00:00Z"
        assert publications.list_page.call_args.args == (RangeListFilters(("Door",), (3,), ()),)
        assert (await client.get("/records/ranges/door", params={"height": 0})).status_code == 400


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
