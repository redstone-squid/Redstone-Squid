"""Public DTO projections preserve wire fields and omit storage metadata."""

from decimal import Decimal
from unittest.mock import create_autospec

import httpx
from litestar import Litestar, get
from litestar.di import Provide

from squid.api.v1.search import PublicSearchFieldDTO, list_search_fields
from squid.api.v1.tags import PublicTagDTO, get_tag, list_tags
from squid.search.application import SearchService
from squid.search.application.fields import FieldDefinition, FieldRegistry, FieldType
from squid.tags.application import TagService
from squid.tags.domain import RecordOperator, TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagDefinition
from tests.support.litestar import asgi_transport


async def test_tag_dto_preserves_public_fields_and_decimal_precision() -> None:
    definition = TagDefinition(
        id=7,
        stable_key="height",
        display_name="Height",
        query_name="height",
        authority=TagAuthority.OFFICIAL,
        semantic_kind=TagSemanticKind.RESTRICTION,
        value_type=TagValueType.NUMERIC,
        restriction_type="size",
        record_operator=RecordOperator.AT_MOST,
        numeric_step=Decimal("0.0000000000000000001"),
        moderation_status=TagModerationStatus.APPROVED,
        created_by_account_id=123,
    )
    tags = create_autospec(TagService, instance=True)
    tags.public_definitions.return_value = (definition,)
    tags.public_definition.return_value = definition
    dependencies = {"tags": Provide(lambda: tags, sync_to_thread=False)}
    app = Litestar(
        [
            get("/tags", return_dto=PublicTagDTO, dependencies=dependencies)(list_tags.fn),
            get("/tags/{tag_id:int}", return_dto=PublicTagDTO, dependencies=dependencies)(get_tag.fn),
        ]
    )
    async with httpx.AsyncClient(transport=asgi_transport(app), base_url="https://test") as client:
        listing = await client.get("/tags")
        detail = await client.get("/tags/7")

    expected = {
        "id": 7,
        "key": "height",
        "name": "Height",
        "query_name": "height",
        "authority": "official",
        "kind": "restriction",
        "value_type": "numeric",
        "restriction_type": "size",
        "record_operator": "at_most",
        "canonical_unit": None,
        "display_unit": None,
        "numeric_step": "1E-19",
    }
    assert detail.status_code == listing.status_code == 200
    assert detail.json() == expected
    assert listing.json() == {"items": [expected], "total": 1, "next": None, "prev": None}
    schema = app.openapi_schema.to_schema()["components"]["schemas"]["TagDetail"]
    assert set(schema["properties"]) == set(expected)


async def test_search_field_dto_exposes_syntax_without_storage_or_coercion_details() -> None:
    service = create_autospec(SearchService, instance=True)
    service.fields.return_value = FieldRegistry(
        [
            FieldDefinition(
                name="height",
                value_type=FieldType.NUMBER,
                supports_range=True,
                supports_sort=True,
                aliases=("h",),
                storage_name="tag:42",
                unit_scales=(("block", Decimal(1)),),
                numeric_step=Decimal("0.5"),
                suggested_values=("private-hint",),
            )
        ]
    )
    handler = get(
        "/fields",
        return_dto=PublicSearchFieldDTO,
        dependencies={"search_service": Provide(lambda: service, sync_to_thread=False)},
    )(list_search_fields.fn)
    app = Litestar([handler])
    async with httpx.AsyncClient(transport=asgi_transport(app), base_url="https://test") as client:
        response = await client.get("/fields")

    assert response.status_code == 200
    assert response.json() == [
        {"name": "height", "type": "number", "supports_range": True, "supports_sort": True, "aliases": ["h"]}
    ]
    schema = app.openapi_schema.to_schema()["components"]["schemas"]["SearchField"]
    assert set(schema["properties"]) == set(response.json()[0])
