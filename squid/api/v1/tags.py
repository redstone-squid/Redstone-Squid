"""Public tag-definition read routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from squid.api.dependencies import CursorSigner, Tags
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.v1.schemas.tags import TagDetail
from squid.core.errors import ErrorCode, NotFoundError, ValidationError

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=Page[TagDetail], responses=responses(400, 422, 503))
async def list_tags(
    tags: Tags,
    signer: CursorSigner,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[TagDetail]:
    """List published tag definitions."""
    definitions = list(await tags.public_definitions())
    offset = _offset(signer, cursor, "tags:approved")
    selected = definitions[offset : offset + page_size]
    next_offset = offset + len(selected)
    has_more = next_offset < len(definitions)
    return Page(
        items=[TagDetail.from_domain(definition) for definition in selected],
        next_cursor=signer.encode({"offset": next_offset}, binding="tags:approved") if has_more else None,
        has_more=has_more,
    )


@router.get("/{tag_id}", response_model=TagDetail, responses=responses(404, 422, 503))
async def get_tag(tag_id: int, tags: Tags) -> TagDetail:
    """Return one published tag definition."""
    definition = await tags.public_definition(tag_id)
    if definition is None:
        msg = "Tag not found."
        raise NotFoundError(msg, resource="tag", public_context={"tag_id": tag_id})
    return TagDetail.from_domain(definition)


def _offset(signer: CursorSigner, cursor: str | None, binding: str) -> int:
    if cursor is None:
        return 0
    value = signer.decode(cursor, binding=binding).get("offset")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = "cursor payload contains an invalid offset"
        raise ValidationError(msg, code=ErrorCode.INVALID_CURSOR)
    return value
