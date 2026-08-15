"""Typeahead route tests."""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Response

from squid.api.security import Principal
from squid.api.v1.suggest import list_sources, suggest
from squid.core.errors import NotFoundError
from squid.suggestions.application import (
    Candidate,
    SuggestionRegistry,
    SuggestionService,
    SuggestionSource,
    candidate,
)
from squid.suggestions.domain import SourceKind, SuggestionRequest, ValueType, Visibility
from squid.suggestions.infrastructure.providers import StaticProvider

VIEW_PENDING = "build.submission.view_pending"

RESTRICTIONS = ["seamless", "semi_seamless", "full_lamp"]


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[SuggestionRequest] = []

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        self.requests.append(request)
        return (candidate("seamless", "Seamless"),)


def service(*sources: SuggestionSource) -> SuggestionService:
    return SuggestionService(SuggestionRegistry.of(sources))


def restrictions_source() -> SuggestionSource:
    return SuggestionSource(
        id="approved_restrictions",
        provider=StaticProvider.of(RESTRICTIONS),
        kind=SourceKind.ENUMERABLE,
        kind_label="restriction",
    )


def gated_source() -> SuggestionSource:
    return SuggestionSource(
        id="builds_pending",
        provider=StaticProvider.labelled([("7", "#7 — pending door")]),
        visibility=Visibility.REQUIRES_NODE,
        required_node=VIEW_PENDING,
        value_type=ValueType.INTEGER,
    )


def request_for(*, allowed: bool = False) -> Any:
    async def allows(subject: object, node: object) -> bool:
        del subject
        return allowed and str(getattr(node, "name", node)) == VIEW_PENDING

    return SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                runtime=SimpleNamespace(services=SimpleNamespace(permissions=SimpleNamespace(allows=allows)))
            )
        ),
    )


def principal(*, anonymous: bool = True) -> Principal:
    if anonymous:
        return Principal(kind="anonymous", subject="anonymous")
    return Principal(kind="account", subject="account:1", nodes=frozenset({"**"}), account_id=1)


async def test_a_source_returns_ranked_items() -> None:
    page = await suggest(
        "approved_restrictions",
        request_for(),
        Response(),
        service(restrictions_source()),
        principal(),
        q="seam",
    )
    assert [item.value for item in page.items] == ["seamless", "semi_seamless"]
    assert page.source == "approved_restrictions"
    assert page.items[0].kind == "restriction"


async def test_an_unknown_source_is_a_not_found_because_it_is_a_bad_url() -> None:
    with pytest.raises(NotFoundError):
        await suggest("nope", request_for(), Response(), service(restrictions_source()), principal())


async def test_an_enumerable_source_carries_an_etag_a_client_can_cache_on() -> None:
    response = Response()
    page = await suggest(
        "approved_restrictions",
        request_for(),
        response,
        service(restrictions_source()),
        principal(),
    )
    assert page.revision is not None
    assert response.headers["ETag"] == f'"{page.revision:x}"'
    assert "private" in response.headers["Cache-Control"]


async def test_a_queried_source_is_not_cached() -> None:
    response = Response()
    queried = SuggestionSource(id="builds", provider=StaticProvider.of(["1"]))
    page = await suggest("builds", request_for(), response, service(queried), principal())
    assert page.revision is None
    assert "ETag" not in response.headers


async def test_a_gated_source_is_empty_for_an_anonymous_caller() -> None:
    page = await suggest("builds_pending", request_for(), Response(), service(gated_source()), principal())
    assert page.items == []


async def test_a_gated_source_is_served_to_a_caller_holding_the_node() -> None:
    page = await suggest(
        "builds_pending",
        request_for(allowed=True),
        Response(),
        service(gated_source()),
        principal(anonymous=False),
    )
    assert [item.value for item in page.items] == ["7"]


async def test_the_limit_is_passed_through_and_bounded() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(id="approved_restrictions", provider=provider)
    await suggest("approved_restrictions", request_for(), Response(), service(source), principal(), q="s", limit=3)
    assert provider.requests[0].limit == 3


async def test_the_category_query_parameter_becomes_request_context() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(id="approved_restrictions", provider=provider)
    await suggest(
        "approved_restrictions",
        request_for(),
        Response(),
        service(source),
        principal(),
        q="s",
        category="door",
    )
    assert provider.requests[0].context == {"category": "door"}


async def test_the_signed_in_account_reaches_viewer_scoped_providers() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(
        id="approved_restrictions",
        provider=provider,
        visibility=Visibility.VIEWER_SCOPED,
    )
    await suggest("approved_restrictions", request_for(), Response(), service(source), principal(anonymous=False))
    assert provider.requests[0].viewer.account_id == 1


async def test_sources_are_published_so_a_client_need_not_hardcode_them() -> None:
    published = await list_sources(service(restrictions_source(), gated_source()))
    by_id = {item.id: item for item in published}
    assert by_id["approved_restrictions"].kind == "enumerable"
    assert by_id["approved_restrictions"].requires_authentication is False
    assert by_id["builds_pending"].requires_authentication is True
    assert by_id["builds_pending"].value_type == "integer"
