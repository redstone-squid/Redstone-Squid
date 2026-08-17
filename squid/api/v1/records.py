"""Computed record read routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import BuildQueries, Records
from squid.api.errors import responses
from squid.api.pagination import (
    AfterIdParam,
    BeforeIdParam,
    OffsetParam,
    Page,
    PageSizeParam,
    parse_page_sort,
    render_page,
    resolve_selector,
)
from squid.api.v1.schemas.builds import BuildSummary
from squid.api.v1.schemas.records import RecordDetail, RecordSummary
from squid.builds.domain import Status
from squid.core.errors import DataIntegrityError
from squid.records.errors import RecordNotFoundError

router = APIRouter(prefix="/records", tags=["records"])
# Result identifiers ascend with computation, so recency needs no separate indexed column.
_SORT_FIELDS = frozenset({"id"})


@router.get(
    "/{record_id}",
    response_model=RecordDetail,
    responses=responses(404, 422, 500, 503),
    operation_id="records_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_record(record_id: int, records: Records, build_queries: BuildQueries) -> RecordDetail:
    """Return one result only while its computation run is active."""
    record = await records.get(record_id)
    if record is None:
        raise RecordNotFoundError(record_id)

    found = await build_queries.get_many(record.holder_build_ids)
    public_builds = {
        build.id: build for build in found if build.id is not None and build.submission_status is Status.CONFIRMED
    }
    unavailable_ids = [build_id for build_id in record.holder_build_ids if build_id not in public_builds]
    if unavailable_ids:
        msg = "A published record references holder builds that are unavailable to the public catalogue."
        raise DataIntegrityError(
            msg,
            context={"record_id": record.id, "unavailable_holder_build_ids": unavailable_ids},
        )

    summary = RecordSummary.from_domain(record)
    return RecordDetail(
        **summary.model_dump(),
        holder_builds=[BuildSummary.from_domain(public_builds[build_id]) for build_id in record.holder_build_ids],
    )


@router.get(
    "",
    response_model=Page[RecordSummary],
    responses=responses(400, 422, 503),
    operation_id="records_list",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def list_records(
    records: Records,
    sort: Annotated[str | None, Query(max_length=80)] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    after_id: AfterIdParam = None,
    before_id: BeforeIdParam = None,
) -> Page[RecordSummary]:
    """List authoritative published record results."""
    _, descending = parse_page_sort(sort, allowed=_SORT_FIELDS, default="-id")
    selector = resolve_selector(offset=offset, after_id=after_id, before_id=before_id)
    page = await records.list_page(selector=selector, descending=descending, page_size=page_size)
    return render_page(page, RecordSummary.from_domain)
