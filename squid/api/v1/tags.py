"""Public tag-definition read routes."""

from fastapi import APIRouter

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Tags
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam, render_page
from squid.api.v1.schemas.tags import TagDetail
from squid.core.pagination import offset_page
from squid.tags.errors import TagNotFoundError

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get(
    "",
    response_model=Page[TagDetail],
    responses=responses(400, 422, 503),
    operation_id="tags_list",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def list_tags(
    tags: Tags,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[TagDetail]:
    """List published tag definitions."""
    definitions = await tags.public_definitions()
    page = offset_page(definitions, offset=offset or 0, page_size=page_size)
    return render_page(page, TagDetail.from_domain)


@router.get(
    "/{tag_id}",
    response_model=TagDetail,
    responses=responses(404, 422, 503),
    operation_id="tags_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_tag(tag_id: int, tags: Tags) -> TagDetail:
    """Return one published tag definition."""
    definition = await tags.public_definition(tag_id)
    if definition is None:
        raise TagNotFoundError(tag_id)
    return TagDetail.from_domain(definition)
