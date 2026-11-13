"""Computed record read routes."""

from typing import Annotated

from litestar import Router, get
from litestar.di import Provide
from litestar.dto import DataclassDTO, DTOConfig
from litestar.params import FromPath, QueryParameter

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import PublicRecords, Records, get_public_records, get_records
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
from squid.api.v1.schemas.builds import BuildSummary
from squid.api.v1.schemas.records import RecordDetail, RecordSummary
from squid.records.application.models import PublishedTitle
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
async def get_record(record_id: FromPath[int], records: PublicRecords) -> RecordDetail:
    """Return one result only while its computation run is active."""
    detail = await records.get(record_id)
    if detail is None:
        raise RecordNotFoundError(record_id)
    summary = RecordSummary.from_domain(detail.result)
    return RecordDetail(
        **summary.model_dump(),
        holder_builds=[BuildSummary.from_public_summary(build) for build in detail.holder_builds],
    )


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


router = Router(
    dependencies={"records": Provide(get_records, sync_to_thread=False)},
    path="/records",
    tags=["records"],
    route_handlers=[get_record, list_records],
)
