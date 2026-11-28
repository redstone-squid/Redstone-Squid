"""Public tag-definition read routes."""

from typing import Annotated

from advanced_alchemy.extensions.litestar.dto import SQLAlchemyDTO, SQLAlchemyDTOConfig
from litestar import Router, get
from litestar.di import Provide
from litestar.params import FromPath, QueryParameter

from squid.api.contract import ANONYMOUS, WEB, contract, transport_only
from squid.api.dependencies import CurrentCaller, Services, Tags, get_tags
from squid.api.errors import responses
from squid.api.pagination import ListResponse, OffsetParam, Page, PageSizeParam
from squid.api.v1.browser_views import BrowserViews, TagDetail
from squid.core.actors import ActingContext
from squid.core.pagination import offset_page
from squid.core.tags.errors import TagNotFoundError
from squid.core.tags.models import TagDefinition


class PublicTagDTO(SQLAlchemyDTO[TagDefinition]):
    """Publish tag vocabulary without moderation, ownership, or relationship fields."""

    __schema_name__ = "TagSummary"
    config = SQLAlchemyDTOConfig(
        include={
            "id",
            "stable_key",
            "slug",
            "display_name",
            "query_name",
            "authority",
            "semantic_kind",
            "value_type",
            "restriction_type",
            "record_operator",
            "canonical_unit",
            "default_display_unit",
            "numeric_step",
        },
        rename_fields={
            "stable_key": "key",
            "display_name": "name",
            "semantic_kind": "kind",
            "default_display_unit": "display_unit",
        },
    )


@get(
    "/",
    responses=responses(400, 422, 503),
    response_class=ListResponse,
    operation_id="tags_list",
    return_dto=PublicTagDTO,
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_tags(
    tags: Tags,
    page_size: PageSizeParam = 50,
    offset: OffsetParam = None,
) -> Page[TagDefinition]:
    """List published tag definitions."""
    definitions = await tags.public_definitions(context=ActingContext.current())
    page = offset_page(definitions, offset=offset or 0, page_size=page_size)
    return Page[TagDefinition].from_domain(page)


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
    route_handlers=[list_tags, get_tag, look_up_tag],
)
