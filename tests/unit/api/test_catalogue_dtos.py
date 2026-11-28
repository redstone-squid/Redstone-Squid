"""Public DTO projections preserve wire fields and omit storage metadata."""

from decimal import Decimal
from unittest.mock import create_autospec

import httpx
from litestar import Litestar, get
from litestar.di import Provide

from squid.api.security import ANONYMOUS
from squid.api.v1.search import list_search_fields
from squid.api.v1.tags import get_tag, list_tags
from squid.core.search.application import SearchService
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry, FieldType
from squid.core.tags.application import TagService
from squid.core.tags.domain import RecordOperator, TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
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
    definition.slug = "height"
    tags = create_autospec(TagService, instance=True)
    tags.public_definitions.return_value = (definition,)
    tags.public_definition.return_value = definition
    dependencies = {"tags": Provide(lambda: tags, sync_to_thread=False)}
    app = Litestar(
        [
            get("/tags", dependencies=dependencies)(list_tags.fn),
            get(
                "/tags/{tag_id:int}",
                dependencies={
                    **dependencies,
                    "caller": Provide(lambda: ANONYMOUS, sync_to_thread=False),
                    "services": Provide(lambda: None, sync_to_thread=False),
                },
            )(get_tag.fn),
        ]
    )
    with ANONYMOUS.context().scope():
        async with httpx.AsyncClient(transport=asgi_transport(app), base_url="https://test") as client:
            listing = await client.get("/tags")
            detail = await client.get("/tags/7")

    expected = {
        "id": 7,
        "slug": "height",
        "aliases": [],
        "build_count": 0,
        "record_count": 0,
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
    details = {**expected, "viewer": None, "description": None, "rules_anchor": None, "revision": definition.revision}
    assert detail.json() == details
    assert listing.json() == {"items": [expected], "total": 1, "next": None, "prev": None}
    schema = app.openapi_schema.to_schema()["components"]["schemas"]["TagDetail"]
    assert set(schema["properties"]) == set(details)


async def test_search_fields_describe_syntax_without_storage_or_coercion_details() -> None:
    kind = DEFAULT_FIELD_REGISTRY.resolve("kind")
    assert kind is not None
    service = create_autospec(SearchService, instance=True)
    service.fields.return_value = FieldRegistry(
        [
            kind,
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
            ),
        ]
    )
    handler = get(
        "/fields",
        dependencies={"search_service": Provide(lambda: service, sync_to_thread=False)},
    )(list_search_fields.fn)
    app = Litestar([handler])
    with ANONYMOUS.context().scope():
        async with httpx.AsyncClient(transport=asgi_transport(app), base_url="https://test") as client:
            response = await client.get("/fields")

    assert response.status_code == 200
    kind_entry, height = response.json()
    assert height == {
        "name": "height",
        "aliases": ["h"],
        "type": "number",
        "label": None,
        "group": "text",
        "unit": None,
        "scopes": ["builds"],
        "supports_range": True,
        "supports_sort": True,
        "metric": False,
        "choices": None,
        "vocabulary": None,
        "examples": [],
    }
    assert kind_entry["vocabulary"] == "search_build_kind"
    assert kind_entry["choices"][0] == {"value": "door", "scopes": ["builds", "records"]}
    assert kind_entry["choices"][-1] == {"value": "other", "scopes": ["builds"]}
    schema = app.openapi_schema.to_schema()["components"]["schemas"]["SearchField"]
    assert set(schema["properties"]) == set(height)
