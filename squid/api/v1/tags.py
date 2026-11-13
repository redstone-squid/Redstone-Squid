"""Public tag-definition read routes."""

from advanced_alchemy.extensions.litestar.dto import SQLAlchemyDTO, SQLAlchemyDTOConfig
from litestar import Router, get
from litestar.di import Provide
from litestar.params import FromPath

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import Tags, get_tags
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageSizeParam
from squid.core.pagination import offset_page
from squid.tags.errors import TagNotFoundError
from squid.tags.models import TagDefinition


class PublicTagDTO(SQLAlchemyDTO[TagDefinition]):
    """Publish tag vocabulary without moderation, ownership, or relationship fields."""

    __schema_name__ = "TagDetail"
    config = SQLAlchemyDTOConfig(
        include={
            "id",
            "stable_key",
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
    definitions = await tags.public_definitions()
    page = offset_page(definitions, offset=offset or 0, page_size=page_size)
    return Page[TagDefinition].from_domain(page)


@get(
    "/{tag_id:int}",
    responses=responses(404, 422, 503),
    operation_id="tags_get",
    return_dto=PublicTagDTO,
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def get_tag(tag_id: FromPath[int], tags: Tags) -> TagDefinition:
    """Return one published tag definition."""
    definition = await tags.public_definition(tag_id)
    if definition is None:
        raise TagNotFoundError(tag_id)
    return definition


router = Router(
    dependencies={"tags": Provide(get_tags, sync_to_thread=False)},
    path="/tags",
    tags=["tags"],
    route_handlers=[list_tags, get_tag],
)
