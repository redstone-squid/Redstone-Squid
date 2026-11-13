"""Public tag-definition read routes."""

from litestar import Router, get
from litestar.params import FromPath

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Tags
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam
from squid.api.v1.schemas.tags import TagDetail
from squid.core.pagination import offset_page
from squid.tags.errors import TagNotFoundError


@get(
    "/",
    responses=responses(400, 422, 503),
    operation_id="tags_list",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def list_tags(
    tags: Tags,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[TagDetail]:
    """List published tag definitions."""
    definitions = await tags.public_definitions()
    page = offset_page(definitions, offset=offset or 0, page_size=page_size)
    return Page[TagDetail].from_domain(page, TagDetail.from_domain)


@get(
    "/{tag_id:int}",
    responses=responses(404, 422, 503),
    operation_id="tags_get",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_tag(tag_id: FromPath[int], tags: Tags) -> TagDetail:
    """Return one published tag definition."""
    definition = await tags.public_definition(tag_id)
    if definition is None:
        raise TagNotFoundError(tag_id)
    return TagDetail.from_domain(definition)


router = Router(path="/tags", tags=["tags"], route_handlers=[list_tags, get_tag])
