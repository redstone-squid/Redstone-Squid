"""Computed record read routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from squid.api.dependencies import BuildQueries, CursorSigner, Records
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.v1.schemas.builds import BuildSummary
from squid.api.v1.schemas.records import RecordDetail, RecordSummary
from squid.builds.domain import Status
from squid.core.errors import DataIntegrityError, ErrorCode, NotFoundError, ValidationError

router = APIRouter(prefix="/records", tags=["records"])
_BINDING = "records:active:id-desc"


@router.get("/{record_id}", response_model=RecordDetail, responses=responses(404, 422, 500, 503))
async def get_record(record_id: int, records: Records, build_queries: BuildQueries) -> RecordDetail:
    """Return one result only while its computation run is active."""
    record = await records.get(record_id)
    if record is None:
        raise NotFoundError(resource="record", public_context={"record_id": record_id})

    found = await build_queries.get_many(record.holder_build_ids)
    public_builds = {
        build.id: build for build in found if build.id is not None and build.submission_status is Status.CONFIRMED
    }
    unavailable_ids = [build_id for build_id in record.holder_build_ids if build_id not in public_builds]
    if unavailable_ids:
        msg = "An active record references holder builds that are unavailable to the public catalogue."
        raise DataIntegrityError(
            msg,
            context={"record_id": record.id, "unavailable_holder_build_ids": unavailable_ids},
        )

    summary = RecordSummary.from_domain(record)
    return RecordDetail(
        **summary.model_dump(),
        holder_builds=[BuildSummary.from_domain(public_builds[build_id]) for build_id in record.holder_build_ids],
    )


@router.get("", response_model=Page[RecordSummary], responses=responses(400, 422, 503))
async def list_records(
    records: Records,
    signer: CursorSigner,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[RecordSummary]:
    """List authoritative active record results."""
    after_id = _after_id(signer, cursor)
    found = list(await records.list_page(after_id=after_id, limit=page_size + 1))
    has_more = len(found) > page_size
    page_records = found[:page_size]
    next_cursor = (
        signer.encode({"after_id": page_records[-1].id}, binding=_BINDING) if has_more and page_records else None
    )
    return Page(
        items=[RecordSummary.from_domain(record) for record in page_records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _after_id(signer: CursorSigner, cursor: str | None) -> int | None:
    if cursor is None:
        return None
    value = signer.decode(cursor, binding=_BINDING).get("after_id")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = "cursor payload contains an invalid record identifier"
        raise ValidationError(msg, code=ErrorCode.INVALID_CURSOR)
    return value
