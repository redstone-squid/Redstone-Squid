"""Public Minecraft-version read routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from squid.api.dependencies import CursorSigner, Services
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.v1.schemas.versions import VersionDetail
from squid.core.errors import ErrorCode, ValidationError

router = APIRouter(prefix="/versions", tags=["versions"])


@router.get("", response_model=Page[VersionDetail], responses=responses(400, 422, 503))
async def list_versions(
    services: Services,
    signer: CursorSigner,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[VersionDetail]:
    """List recognized Java and Bedrock releases."""
    versions = await services.versions.list_all()
    offset = _offset(signer, cursor)
    selected = versions[offset : offset + page_size]
    next_offset = offset + len(selected)
    has_more = next_offset < len(versions)
    return Page(
        items=[VersionDetail.from_domain(version) for version in selected],
        next_cursor=signer.encode({"offset": next_offset}, binding="versions:all") if has_more else None,
        has_more=has_more,
    )


def _offset(signer: CursorSigner, cursor: str | None) -> int:
    if cursor is None:
        return 0
    value = signer.decode(cursor, binding="versions:all").get("offset")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = "cursor payload contains an invalid offset"
        raise ValidationError(msg, code=ErrorCode.INVALID_CURSOR)
    return value
