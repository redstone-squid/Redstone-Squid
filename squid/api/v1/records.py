"""Computed record read routes."""

from typing import Annotated

from litestar import Router, get, post
from litestar.di import Provide
from litestar.dto import DataclassDTO, DTOConfig
from litestar.params import FromPath, JSONBody, QueryParameter

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import (
    PublicRecords,
    RangePublications,
    Records,
    get_public_records,
    get_range_publication,
    get_records,
)
from squid.api.errors import responses
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    parse_page_sort,
    resolve_selector,
)
from squid.records.application.expansion_regions import DoorRangeQuery, DoorRangeResult
from squid.records.application.live import DoorRecordLookup, LiveDoorRecord
from squid.records.application.models import PublicRecordDetail, PublishedTitle
from squid.records.application.range_snapshots import RangePublication
from squid.records.errors import RecordNotFoundError


class PublicRecordDTO(DataclassDTO[PublishedTitle]):
    """Serialize published record facts directly without another Python record model."""

    __schema_name__ = "PublishedTitle"

    config = DTOConfig(
        include={
            "id",
            "definition_id",
            "title_id",
            "title",
            "subtitle",
            "base_title",
            "build_kind",
            "version_scope",
            "status",
            "holder_build_ids",
            "computed_at",
        }
    )


# Result identifiers ascend with computation, so recency needs no separate indexed column.
_SORT_FIELDS = frozenset({"id"})


@get(
    "/{record_id:int}",
    responses=responses(404, 422, 500, 503),
    operation_id="records_get",
    dependencies={"records": Provide(get_public_records, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_record(record_id: FromPath[int], records: PublicRecords) -> PublicRecordDetail:
    """Return one result only while its computation run is active."""
    detail = await records.get(record_id)
    if detail is None:
        raise RecordNotFoundError(record_id)
    return detail


@get(
    "/",
    responses=responses(400, 422, 503),
    operation_id="records_list",
    return_dto=PublicRecordDTO,
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_records(
    records: Records,
    sort: Annotated[str | None, QueryParameter(max_length=80)] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[PublishedTitle]:
    """List authoritative published record results."""
    _, descending = parse_page_sort(sort, allowed=_SORT_FIELDS, default="-id")
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    page = await records.list_page(selector=selector, descending=descending, page_size=page_size)
    return Page[PublishedTitle].from_domain(page)


@post(
    "/lookup/door",
    status_code=200,
    responses=responses(400, 422, 503),
    operation_id="records_lookup_door",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def lookup_door(data: JSONBody[DoorRecordLookup], records: Records) -> LiveDoorRecord:
    """Compare fixed doors and expandable configurations without creating a title."""
    return await records.lookup_door(data)


@post(
    "/lookup/door/ranges",
    status_code=200,
    responses=responses(400, 422, 503),
    operation_id="records_lookup_door_ranges",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def lookup_door_ranges(data: JSONBody[DoorRangeQuery], records: Records) -> DoorRangeResult:
    """Find smallest-record width ranges at fixed height and depth.

    Results are live and never published. Unsupported inputs, exhausted limits,
    and timeouts return an analysis status with no awarded ranges.
    """
    return await records.lookup_door_ranges(data)


@get(
    "/ranges/door/{query_id:int}",
    responses=responses(404, 422, 503),
    operation_id="records_get_door_ranges",
    dependencies={"publications": Provide(get_range_publication, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_door_ranges(query_id: FromPath[int], publications: RangePublications) -> RangePublication:
    """Read a registered query's active publication and latest computation attempt."""
    publication = await publications.get(query_id)
    if publication is None:
        raise RecordNotFoundError(query_id)
    return publication


router = Router(
    dependencies={"records": Provide(get_records, sync_to_thread=False)},
    path="/records",
    tags=["records"],
    route_handlers=[get_record, list_records, lookup_door, lookup_door_ranges, get_door_ranges],
)
