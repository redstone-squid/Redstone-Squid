"""Public tag-definition read routes."""

from fastapi import APIRouter

from squid.api.dependencies import Tags
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam, render_page
from squid.api.v1.schemas.tags import TagDetail
from squid.core.errors import NotFoundError
from squid.core.pagination import offset_page

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=Page[TagDetail], responses=responses(400, 422, 503))
async def list_tags(
    tags: Tags,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[TagDetail]:
    """List published tag definitions."""
    definitions = await tags.public_definitions()
    page = offset_page(definitions, offset=offset or 0, page_size=page_size)
    return render_page(page, TagDetail.from_domain)


@router.get("/{tag_id}", response_model=TagDetail, responses=responses(404, 422, 503))
async def get_tag(tag_id: int, tags: Tags) -> TagDetail:
    """Return one published tag definition."""
    definition = await tags.public_definition(tag_id)
    if definition is None:
        msg = "Tag not found."
        raise NotFoundError(msg, resource="tag", public_context={"tag_id": tag_id})
    return TagDetail.from_domain(definition)
