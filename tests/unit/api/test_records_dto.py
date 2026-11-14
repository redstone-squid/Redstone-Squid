"""Published records serialize through Litestar without an intermediate API model."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import create_autospec
from uuid import UUID

import httpx
from litestar import Litestar, get
from litestar.di import Provide

from squid.api.v1.records import PublicRecordDTO, get_record, list_records
from squid.builds.application import PublicBuildDimensions, PublicBuildPreview, PublicBuildSummary, PublicBuildTag
from squid.core.pagination import Page, PageAnchor
from squid.records.application import PublicRecordQueryService, RecordService
from squid.records.application.models import PublicRecordDetail, PublishedTitle
from squid.records.domain import BaseTitle, BuildKind, ResolutionStatus, VersionScope

_RECORD = PublishedTitle(
    id=7,
    definition_id=3,
    title_id=UUID("22222222-2222-2222-2222-222222222222"),
    title="Fastest 2x3 door",
    subtitle="All versions",
    base_title=BaseTitle.FASTEST,
    build_kind=BuildKind.DOOR,
    version_scope=VersionScope.ALL_TIME,
    status=ResolutionStatus.RESOLVED,
    holder_build_ids=(42,),
    computed_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
)
_RECORD_JSON = {
    "id": 7,
    "definition_id": 3,
    "title_id": "22222222-2222-2222-2222-222222222222",
    "title": "Fastest 2x3 door",
    "subtitle": "All versions",
    "base_title": BaseTitle.FASTEST.value,
    "build_kind": BuildKind.DOOR.value,
    "version_scope": VersionScope.ALL_TIME.value,
    "status": ResolutionStatus.RESOLVED.value,
    "holder_build_ids": [42],
    "computed_at": "2026-08-10T12:00:00Z",
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
    app = Litestar([handler])

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
    public = schemas["PublishedTitle"]
    assert set(public["properties"]) == PublicRecordDTO.config.include
    assert public["properties"]["holder_build_ids"]["type"] == "array"
    assert public["properties"]["computed_at"]["format"] == "date-time"
    assert public["properties"]["title_id"]["format"] == "uuid"


async def test_record_detail_serializes_the_application_read_model() -> None:
    holder = PublicBuildSummary(
        id=42,
        revision=3,
        title="Fastest 2x3 door",
        display_name=None,
        status="confirmed",
        category="Door",
        dimensions=PublicBuildDimensions(width=5, height=6, depth=None),
        creators=("Alice",),
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
    records.get.return_value = PublicRecordDetail.with_holders(_RECORD, [holder])
    handler = get(
        "/records/{record_id:int}",
        dependencies={"records": Provide(lambda: records, sync_to_thread=False)},
    )(get_record.fn)
    app = Litestar([handler])

    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/records/7")

    assert response.status_code == 200
    assert response.json() == {
        **_RECORD_JSON,
        "holder_builds": [
            {
                "id": 42,
                "revision": 3,
                "title": "Fastest 2x3 door",
                "display_name": None,
                "status": "confirmed",
                "category": "Door",
                "dimensions": {"width": 5, "height": 6, "depth": None},
                "creators": ["Alice"],
                "credits": [],
                # Decimal tag values travel as strings so clients keep their exact precision.
                "tags": [{"key": "official_delay", "name": "Delay", "value": "1.5", "unit": "second"}],
                "preview": {"kind": "render", "url": "https://media.example/render.png"},
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
