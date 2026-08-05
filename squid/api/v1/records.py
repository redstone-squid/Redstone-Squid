"""Computed record read routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from squid.api.dependencies import CursorSigner, Services
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.v1.schemas.records import RecordDetail, RecordSummary
from squid.core.errors import ErrorCode, NotFoundError, ValidationError

router = APIRouter(prefix="/records", tags=["records"])
_BINDING = "records:active:id-desc"


@router.get("/{record_id}", response_model=RecordDetail, responses=responses(404, 422, 503))
async def get_record(record_id: int, services: Services) -> RecordDetail:
    """Return one result only while its computation run is active."""
    record = await services.records.get(record_id)
    if record is None:
        raise NotFoundError(resource="record", public_context={"record_id": record_id})
    return RecordDetail(**RecordSummary.from_domain(record).model_dump())


@router.get("", response_model=Page[RecordSummary], responses=responses(400, 422, 503))
async def list_records(
    services: Services,
    signer: CursorSigner,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[RecordSummary]:
    """List authoritative active record results."""
    after_id = _after_id(signer, cursor)
    records = list(await services.records.list_page(after_id=after_id, limit=page_size + 1))
    has_more = len(records) > page_size
    page_records = records[:page_size]
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
