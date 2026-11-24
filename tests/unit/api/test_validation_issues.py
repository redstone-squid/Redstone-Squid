"""Validation failures retain actionable facts without exposing submitted input."""

import httpx
import pytest
from litestar import Litestar, post
from litestar.params import JSONBody
from pydantic import BaseModel, Field

from squid.api.errors import exception_handlers
from squid.api.v1.schemas.notifications import TagPredicateInput
from squid.core.accounts.domain.profiles import ProfileLink, ProfileUpdate
from squid.core.errors import ValidationError
from tests.support.litestar import asgi_transport


class Row(BaseModel):
    name: str = Field(min_length=4)


class Payload(BaseModel):
    rows: list[Row]
    tags: tuple[TagPredicateInput, ...] = ()


@post("/rows")
async def rows(data: JSONBody[Payload]) -> None:
    pass


@post("/finalize")
async def finalize() -> None:
    raise ValidationError


async def test_decoder_reports_nested_paths_and_safe_params() -> None:
    app = Litestar([rows, finalize], exception_handlers=exception_handlers())
    async with httpx.AsyncClient(transport=asgi_transport(app), base_url="http://test") as client:
        response = await client.post("/rows", json={"rows": [{"name": "XYZ"}, {}]})
        assert response.status_code == 422
        assert response.json()["context"]["fields"] == [
            {"field": "/body/rows/0/name", "code": "too_short", "params": {"min_length": 4}},
            {"field": "/body/rows/1/name", "code": "required", "params": {}},
        ]
        assert "XYZ" not in response.text
        nested = await client.post("/rows", json={"rows": [], "tags": [{"tag_id": 1, "operator": "exact"}]})
        assert nested.json()["context"]["fields"] == [{"field": "/body/tags/0/value", "code": "required", "params": {}}]
        assert (await client.post("/finalize")).json()["context"]["fields"] == []


def test_profile_validation_identifies_the_link_to_correct() -> None:
    change = ProfileUpdate(links=(ProfileLink("First", "https://example.com"), ProfileLink("", "")))
    with pytest.raises(ValidationError) as raised:
        change.validated()
    assert [issue.model_dump(mode="json") for issue in raised.value.field_issues] == [
        {"field": "links/1/label", "code": "required", "params": {}}
    ]
