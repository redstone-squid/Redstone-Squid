"""Computed record read routes."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from litestar import Router, get, post
from litestar.di import Provide
from litestar.dto import DataclassDTO, DTOConfig
from litestar.params import FromPath, JSONBody, PathParameter, QueryParameter

from squid.api.contract import ANONYMOUS, DEVICE, SERVICE, WEB_WRITE, contract, transport_only
from squid.api.dependencies import (
    PublicRecords,
    RangePublications,
    Records,
    get_public_records,
    get_range_publication,
    get_records,
)
from squid.api.errors import responses
from squid.api.idempotency import reserve_idempotent_before_request
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    parse_page_sort,
    resolve_selector,
)
from squid.api.security import requires
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_REBUILD
from squid.core.records.application.expansion_regions import DoorRangeQuery, DoorRangeResult
from squid.core.records.application.live import DoorRecordLookup, LiveDoorRecord
from squid.core.records.application.models import PublicRecordDetail, PublishedRecordResult
from squid.core.records.application.published_points import PublishedDoorPoint
from squid.core.records.application.range_snapshots import RangePublication
from squid.core.records.errors import RecordNotFoundError


class PublicRecordDTO(DataclassDTO[PublishedRecordResult]):
    """Serialize published record facts directly without another Python record model."""

    __schema_name__ = "PublishedRecordResult"

    config = DTOConfig(
        include={
            "result_id",
            "definition_id",
            "record_id",
            "title",
            "subtitle",
            "criterion",
            "build_kind",
            "version_scope",
            "status",
            "holder_build_ids",
            "computed_at",
            "last_checked_at",
            "refresh_pending",
            "version_id",
        }
    )


# Result identifiers ascend with computation, so recency needs no separate indexed column.
_SORT_FIELDS = frozenset({"result_id"})


@get(
    "/{record_id:uuid}",
    responses=responses(404, 422, 500, 503),
    operation_id="records_get",
    dependencies={"records": Provide(get_public_records, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_record(record_id: FromPath[UUID], records: PublicRecords) -> PublicRecordDetail:
    """Return the latest published result for a record."""
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
) -> Page[PublishedRecordResult]:
    """List authoritative published record results."""
    _, descending = parse_page_sort(sort, allowed=_SORT_FIELDS, default="-result_id")
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    page = await records.list_page(selector=selector, descending=descending, page_size=page_size)
    return Page[PublishedRecordResult].from_domain(page)


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


@dataclass(frozen=True, slots=True)
class DoorRangeReference:
    query_id: int


@post(
    "/ranges/door",
    status_code=200,
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="records_register_door_ranges",
    dependencies={"publications": Provide(get_range_publication, sync_to_thread=False)},
    before_request=reserve_idempotent_before_request,
    guards=[requires(RECORD_ENTRY_REBUILD)],
    security=[SERVICE, WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=("record.entry.rebuild",)),
)
async def register_door_ranges(data: JSONBody[DoorRangeQuery], publications: RangePublications) -> DoorRangeReference:
    """Register a query for automatic publication, reusing equivalent registrations.

    Only a new registration queues initial computation. Use refresh to request
    another attempt for an existing query.
    """
    return DoorRangeReference(await publications.register(data))


@post(
    "/ranges/door/{query_id:int}/refresh",
    status_code=202,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="records_refresh_door_ranges",
    dependencies={"publications": Provide(get_range_publication, sync_to_thread=False)},
    before_request=reserve_idempotent_before_request,
    guards=[requires(RECORD_ENTRY_REBUILD)],
    security=[SERVICE, WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=("record.entry.rebuild",)),
)
async def refresh_door_ranges(query_id: FromPath[int], publications: RangePublications) -> DoorRangeReference:
    """Queue recomputation; the current publication remains readable until replacement."""
    if not await publications.request_refresh(query_id):
        raise RecordNotFoundError(query_id)
    return DoorRangeReference(query_id)


@get(
    "/ranges/door/{query_id:int}/at/{width:int}",
    responses=responses(404, 422, 500, 503),
    operation_id="records_get_door_range_point",
    dependencies={"publications": Provide(get_range_publication, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_door_range_point(
    query_id: FromPath[int],
    width: Annotated[int, PathParameter(ge=1, le=2**31 - 1)],
    publications: RangePublications,
) -> PublishedDoorPoint:
    """Read one opening from saved regions, with publication and latest-attempt origin."""
    point = await publications.get_point(query_id, width)
    if point is None:
        raise RecordNotFoundError(query_id)
    return point


router = Router(
    dependencies={"records": Provide(get_records, sync_to_thread=False)},
    path="/records",
    tags=["records"],
    route_handlers=[
        get_record,
        list_records,
        lookup_door,
        lookup_door_ranges,
        get_door_ranges,
        register_door_ranges,
        refresh_door_ranges,
        get_door_range_point,
    ],
)
