"""Public tag-definition read routes."""

from typing import Annotated

from litestar import Router, get
from litestar.di import Provide
from litestar.params import FromPath, FromQuery, QueryParameter

from squid.api.contract import ANONYMOUS, WEB, contract, transport_only
from squid.api.dependencies import CurrentCaller, Services, Tags, get_tags
from squid.api.errors import responses
from squid.api.pagination import CursorParam, ListResponse, OffsetParam, Page, PageSizeParam, resolve_selector
from squid.api.v1 import tag_proposals
from squid.api.v1.browser_views import BrowserViews, TagDetail, TagSummary
from squid.core.actors import ActingContext
from squid.core.tags.domain import TagAuthority, TagSemanticKind
from squid.core.tags.errors import TagNotFoundError
from squid.core.tags.models import TagListSort


@get(
    "/",
    responses=responses(400, 422, 503),
    response_class=ListResponse,
    operation_id="tags_list",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_tags(
    tags: Tags,
    kind: FromQuery[TagSemanticKind | None] = None,
    authority: FromQuery[TagAuthority | None] = None,
    q: Annotated[str, QueryParameter(description="Case-insensitive display name or alias prefix.")] = "",
    page_size: PageSizeParam = 50,
    sort: FromQuery[TagListSort] = TagListSort.DISPLAY,
    cursor: CursorParam = None,
    offset: OffsetParam = None,
) -> Page[TagSummary]:
    """List published tag definitions."""
    definitions = await tags.public_definitions(kind=kind, authority=authority, q=q, context=ActingContext.current())
    page = sort.page(definitions, selector=resolve_selector(offset=offset, cursor=cursor), page_size=page_size)
    return Page[TagSummary].from_domain(page, TagSummary.from_definition)


@get(
    "/{tag_id:int}",
    responses=responses(404, 422, 503),
    operation_id="tags_get",
    response_headers={"Cache-Control": "private, no-store"},
    security=[ANONYMOUS, WEB],
    opt=contract(cli=transport_only()),
)
async def get_tag(tag_id: FromPath[int], tags: Tags, caller: CurrentCaller, services: Services) -> TagDetail:
    """Return one published tag definition."""
    definition = await tags.public_definition(tag_id, context=ActingContext.current())
    if definition is None:
        raise TagNotFoundError(tag_id)
    return await BrowserViews(services, caller).tag(definition)


@get(
    "/lookup",
    responses=responses(404, 422, 503),
    operation_id="tags_lookup",
    response_headers={"Cache-Control": "private, no-store"},
    security=[ANONYMOUS, WEB],
    opt=contract(cli=transport_only()),
)
async def look_up_tag(
    slug: Annotated[str, QueryParameter(min_length=1)], tags: Tags, caller: CurrentCaller, services: Services
) -> TagDetail:
    """Resolve a current or former slug to its published tag."""
    definition = await tags.public_definition(slug, context=ActingContext.current())
    if definition is None:
        raise TagNotFoundError(slug)
    return await BrowserViews(services, caller).tag(definition)


router = Router(
    dependencies={"tags": Provide(get_tags, sync_to_thread=False)},
    path="/tags",
    tags=["tags"],
    route_handlers=[
        list_tags,
        get_tag,
        look_up_tag,
        tag_proposals.list_proposals,
        tag_proposals.create_proposal,
        tag_proposals.approve,
        tag_proposals.reject,
        tag_proposals.archive,
        tag_proposals.add_alias,
    ],
)
