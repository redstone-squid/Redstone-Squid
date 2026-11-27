"""Computed record read routes."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from litestar import Router, get, post
from litestar.di import Provide
from litestar.params import FromPath, FromQuery, JSONBody, PathParameter, QueryParameter

from squid.api.contract import ANONYMOUS, DEVICE, SERVICE, WEB, WEB_WRITE, contract, transport_only
from squid.api.dependencies import (
    CurrentCaller,
    PublicRecords,
    RangePublications,
    RecordAdministration,
    Records,
    Services,
    get_public_records,
    get_range_publication,
    get_record_administration,
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
    resolve_selector,
)
from squid.api.security import requires
from squid.api.v1.browser_views import BrowserViews
from squid.core.builds.domain import DoorOrientationLiteral
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.core.records.application.expansion_regions import DoorRangeQuery, DoorRangeResult
from squid.core.records.application.models import (
    PublicRecordDetail,
    PublicRecordSummary,
    RecomputeItem,
    RecordDrift,
    RecordInspection,
    RecordListFilters,
    RecordListSort,
    RecordStatus,
)
from squid.core.records.application.published_points import PublishedDoorPoint
from squid.core.records.application.range_ports import RangeListFilters
from squid.core.records.application.range_snapshots import RangePublication, RangeRegistration
from squid.core.records.domain import BuildKind, RecordCriterion, VersionScope
from squid.core.records.errors import RecordNotFoundError


@get(
    "/{record_id:uuid}",
    responses=responses(404, 422, 500, 503),
    operation_id="records_get",
    response_headers={"Cache-Control": "private, no-store"},
    dependencies={"records": Provide(get_public_records, sync_to_thread=False)},
    security=[ANONYMOUS, WEB],
    opt=contract(cli=transport_only()),
)
async def get_record(
    record_id: FromPath[UUID], records: PublicRecords, services: Services, caller: CurrentCaller
) -> PublicRecordDetail:
    """Return a record's current result, or its retired state with its holder history.

    A record retires when no build competes for it, and keeps its ID and history
    in case a competitor brings it back.
    """
    detail = await records.get(record_id, context=caller.context())
    if detail is None:
        raise RecordNotFoundError(record_id)
    return await BrowserViews(services, caller).record(detail)


@get(
    "/",
    responses=responses(400, 422, 503),
    operation_id="records_list",
    dependencies={"records": Provide(get_public_records, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_records(
    records: PublicRecords,
    caller: CurrentCaller,
    build_kind: FromQuery[BuildKind | None] = None,
    criterion: FromQuery[RecordCriterion | None] = None,
    version_scope: FromQuery[VersionScope | None] = None,
    status: FromQuery[RecordStatus | None] = None,
    tag_id: FromQuery[list[int] | None] = None,
    tag_slug: FromQuery[list[str] | None] = None,
    sort: FromQuery[RecordListSort] = RecordListSort.ID,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[PublicRecordSummary]:
    """List current record results with their visible holders.

    Every tag_id and tag_slug must be one of the record's types or restrictions.
    Retired records are not listed; search finds them with `record_state:retired`.
    Door evidence is left out, so it is always null here; read it from the record.
    """
    page = await records.list_page(
        selector=resolve_selector(offset=offset, after_id=after_id, before_id=before_id),
        sort=sort,
        filters=RecordListFilters(
            build_kind,
            criterion,
            version_scope,
            status,
            tag_ids=tuple(dict.fromkeys(tag_id or ())),
            tag_slugs=tuple(dict.fromkeys(tag_slug or ())),
        ),
        page_size=page_size,
        context=caller.context(),
    )
    return Page[PublicRecordSummary].from_domain(page)


@post(
    "/lookup/door/ranges",
    status_code=200,
    responses=responses(400, 422, 503),
    operation_id="records_lookup_door_ranges",
    dependencies={"publications": Provide(get_range_publication, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def lookup_door_ranges(
    data: JSONBody[DoorRangeQuery], publications: RangePublications, caller: CurrentCaller
) -> DoorRangeResult:
    """Find smallest-record width ranges at fixed height and depth.

    Results are live and never published. Unsupported inputs, exhausted limits,
    and timeouts return an analysis status with no awarded ranges.
    """
    return await publications.lookup(data, context=caller.context())


@get(
    "/ranges/door",
    responses=responses(400, 422, 503),
    operation_id="records_list_door_ranges",
    dependencies={"publications": Provide(get_range_publication, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_door_ranges(
    publications: RangePublications,
    caller: CurrentCaller,
    orientation: FromQuery[DoorOrientationLiteral | None] = None,
    height: Annotated[int | None, QueryParameter(ge=1, le=2**31 - 1)] = None,
    depth: Annotated[int | None, QueryParameter(ge=1, le=2**31 - 1)] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[RangeRegistration]:
    """List registered door range queries newest first, with the names of their tags and version."""
    page = await publications.list_page(
        RangeListFilters(orientation, height, depth),
        selector=resolve_selector(offset=offset, after_id=after_id, before_id=before_id),
        page_size=page_size,
        context=caller.context(),
    )
    return Page[RangeRegistration].from_domain(page)


@get(
    "/ranges/door/{query_id:int}",
    responses=responses(404, 422, 503),
    operation_id="records_get_door_ranges",
    dependencies={"publications": Provide(get_range_publication, sync_to_thread=False)},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_door_ranges(
    query_id: FromPath[int], publications: RangePublications, caller: CurrentCaller
) -> RangePublication:
    """Read a registered query's active publication and latest computation attempt."""
    publication = await publications.get(query_id, context=caller.context())
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
async def register_door_ranges(
    data: JSONBody[DoorRangeQuery], publications: RangePublications, caller: CurrentCaller
) -> DoorRangeReference:
    """Register a query for automatic publication, reusing equivalent registrations.

    Only a new registration queues initial computation. Use refresh to request
    another attempt for an existing query.
    """
    return DoorRangeReference(await publications.register(data, context=caller.context()))


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
async def refresh_door_ranges(
    query_id: FromPath[int], publications: RangePublications, caller: CurrentCaller
) -> DoorRangeReference:
    """Queue recomputation; the current publication remains readable until replacement."""
    if not await publications.request_refresh(query_id, context=caller.context()):
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
    caller: CurrentCaller,
) -> PublishedDoorPoint:
    """Read one opening from saved regions, with publication and latest-attempt origin."""
    point = await publications.get_point(query_id, width, context=caller.context())
    if point is None:
        raise RecordNotFoundError(query_id)
    return point


@post(
    "/{record_id:uuid}/recompute",
    status_code=202,
    responses=responses(401, 403, 404, 409, 422, 503),
    operation_id="records_recompute",
    before_request=reserve_idempotent_before_request,
    guards=[requires(RECORD_ENTRY_INSPECT)],
    security=[SERVICE, WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=("record.entry.inspect",)),
)
async def recompute_record(record_id: FromPath[UUID], records: Records, caller: CurrentCaller) -> None:
    """Queue every criterion of the record's category for recomputation."""
    if not await records.request_recompute(record_id, context=caller.context()):
        raise RecordNotFoundError(record_id)


@get(
    "/{record_id:uuid}/inspection",
    responses=responses(401, 403, 404, 422, 503),
    operation_id="records_inspect",
    response_headers={"Cache-Control": "private, no-store"},
    guards=[requires(RECORD_ENTRY_INSPECT)],
    security=[SERVICE, WEB, DEVICE],
    opt=contract(cli=transport_only(), scopes=("record.entry.inspect",)),
)
async def inspect_record(record_id: FromPath[UUID], records: Records, caller: CurrentCaller) -> RecordInspection:
    """Compute the record from current builds, without publishing, and compare it with its published result."""
    inspection = await records.inspect(record_id, context=caller.context())
    if inspection is None:
        raise RecordNotFoundError(record_id)
    return inspection


@dataclass(frozen=True, slots=True)
class RecomputeRequest:
    kinds: tuple[BuildKind, ...] = (BuildKind.DOOR, BuildKind.EXTENDER)


@post(
    "/recomputes",
    status_code=202,
    responses=responses(400, 401, 403, 409, 422, 503),
    operation_id="records_request_backstop",
    dependencies={"administration": Provide(get_record_administration, sync_to_thread=False)},
    before_request=reserve_idempotent_before_request,
    guards=[requires(RECORD_ENTRY_REBUILD)],
    security=[SERVICE, WEB_WRITE, DEVICE],
    opt=contract(cli=transport_only(), scopes=("record.entry.rebuild",)),
)
async def request_backstop(
    data: JSONBody[RecomputeRequest], administration: RecordAdministration, caller: CurrentCaller
) -> None:
    """Queue a full recompute that reports each difference no pending change explains as drift."""
    await administration.request_backstop(kinds=data.kinds, context=caller.context())


@get(
    "/recompute-queue",
    responses=responses(401, 403, 422, 503),
    operation_id="records_recompute_queue",
    response_headers={"Cache-Control": "private, no-store"},
    guards=[requires(RECORD_ENTRY_INSPECT)],
    security=[SERVICE, WEB, DEVICE],
    opt=contract(cli=transport_only(), scopes=("record.entry.inspect",)),
)
async def recompute_queue(records: Records, caller: CurrentCaller) -> tuple[RecomputeItem, ...]:
    """List pending and failed recompute requests, oldest first, with each one's last error."""
    return tuple(await records.recompute_queue(context=caller.context()))


@get(
    "/drift",
    responses=responses(400, 401, 403, 422, 503),
    operation_id="records_drift",
    response_headers={"Cache-Control": "private, no-store"},
    guards=[requires(RECORD_ENTRY_INSPECT)],
    security=[SERVICE, WEB, DEVICE],
    opt=contract(cli=transport_only(), scopes=("record.entry.inspect",)),
)
async def list_drift(
    records: Records,
    caller: CurrentCaller,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[RecordDrift]:
    """List records the backstop changed although no queued change explained it, newest first.

    Each entry means the affected-set calculation missed a case.
    """
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    page = await records.drift(selector=selector, page_size=page_size, context=caller.context())
    return Page[RecordDrift].from_domain(page)


router = Router(
    dependencies={"records": Provide(get_records, sync_to_thread=False)},
    path="/records",
    tags=["records"],
    route_handlers=[
        get_record,
        list_records,
        lookup_door_ranges,
        list_door_ranges,
        get_door_ranges,
        register_door_ranges,
        refresh_door_ranges,
        get_door_range_point,
        recompute_record,
        inspect_record,
        request_backstop,
        recompute_queue,
        list_drift,
    ],
)
