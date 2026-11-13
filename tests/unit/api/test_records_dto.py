"""Published records serialize through Litestar without an intermediate summary model."""

from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import UUID

import httpx
from litestar import Litestar, get
from litestar.di import Provide

from squid.api.v1.records import PublicRecordDTO, list_records
from squid.api.v1.schemas.records import RecordSummary
from squid.core.pagination import Page, PageAnchor
from squid.records.application import RecordService
from squid.records.application.models import PublishedTitle
from squid.records.domain import BaseTitle, BuildKind, ResolutionStatus, VersionScope


async def test_record_listing_dto_preserves_json_and_declares_public_fields() -> None:
    record = PublishedTitle(
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
        "items": [RecordSummary.from_domain(record).model_dump(mode="json")],
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
