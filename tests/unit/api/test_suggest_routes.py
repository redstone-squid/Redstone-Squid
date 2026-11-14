"""Typeahead route tests."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast, override

import pytest
from litestar import Litestar, Request
from litestar.types import Scope

from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.suggest import list_sources, suggest
from squid.core.actors import Actor
from squid.core.errors import NotFoundError
from squid.permissions.application import PermissionService
from squid.permissions.domain import Decision, PermissionNode, Reason
from squid.suggestions.application import (
    Candidate,
    SuggestionRegistry,
    SuggestionService,
    SuggestionSource,
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
        return (Candidate.create("seamless", "Seamless"),)


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


class PermissionAnswer(PermissionService):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    @override
    async def allows(self, subject: Actor, node: PermissionNode | str) -> bool:
        del subject
        return self.allowed and str(getattr(node, "name", node)) == VIEW_PENDING

    @override
    async def decisions(self, subject: Actor, nodes: Iterable[PermissionNode | str]) -> tuple[Decision, ...]:
        return tuple([Decision(str(node), await self.allows(subject, node), Reason.DEFAULT) for node in nodes])


@dataclass(frozen=True, slots=True)
class Services:
    permissions: PermissionService


@dataclass(frozen=True, slots=True)
class Runtime:
    services: Services


def request_for(*, allowed: bool = False) -> Request:
    app = Litestar([])
    app.state.runtime = Runtime(Services(PermissionAnswer(allowed)))
    scope = cast(Scope, {"type": "http", "litestar_app": app, "headers": []})

    return Request(scope)


def caller(*, anonymous: bool = True) -> Caller:
    if anonymous:
        return Caller(kind="anonymous", subject="anonymous")
    return Caller(kind="account", subject="account:1", nodes=UNBOUNDED, account_id=1)


async def test_a_source_returns_ranked_items() -> None:
    response = await suggest.fn(
        "approved_restrictions",
        request_for(),
        service(restrictions_source()),
        caller(),
        q="seam",
    )
    assert [item.value for item in response.content.items] == ["seamless", "semi_seamless"]
    assert response.content.source == "approved_restrictions"
    assert response.content.items[0].kind == "restriction"


async def test_an_unknown_source_is_a_not_found_because_it_is_a_bad_url() -> None:
    with pytest.raises(NotFoundError):
        await suggest.fn("nope", request_for(), service(restrictions_source()), caller())


async def test_an_enumerable_source_carries_an_etag_a_client_can_cache_on() -> None:
    response = await suggest.fn(
        "approved_restrictions",
        request_for(),
        service(restrictions_source()),
        caller(),
    )
    assert response.content.revision is not None
    assert response.headers["ETag"] == f'"{response.content.revision:x}"'
    assert "private" in response.headers["Cache-Control"]


async def test_a_queried_source_is_not_cached() -> None:
    queried = SuggestionSource(id="builds", provider=StaticProvider.of(["1"]))
    response = await suggest.fn("builds", request_for(), service(queried), caller())
    assert response.content.revision is None
    assert "ETag" not in response.headers


async def test_a_gated_source_is_empty_for_an_anonymous_caller() -> None:
    response = await suggest.fn("builds_pending", request_for(), service(gated_source()), caller())
    assert response.content.items == []


async def test_a_gated_source_is_served_to_a_caller_holding_the_node() -> None:
    response = await suggest.fn(
        "builds_pending",
        request_for(allowed=True),
        service(gated_source()),
        caller(anonymous=False),
    )
    assert [item.value for item in response.content.items] == ["7"]


async def test_the_limit_is_passed_through_and_bounded() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(id="approved_restrictions", provider=provider)
    await suggest.fn("approved_restrictions", request_for(), service(source), caller(), q="s", limit=3)
    assert provider.requests[0].limit == 3


async def test_the_category_query_parameter_becomes_request_context() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(id="approved_restrictions", provider=provider)
    await suggest.fn(
        "approved_restrictions",
        request_for(),
        service(source),
        caller(),
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
    await suggest.fn("approved_restrictions", request_for(), service(source), caller(anonymous=False))
    assert provider.requests[0].viewer.account_id == 1


async def test_sources_are_published_so_a_client_need_not_hardcode_them() -> None:
    published = await list_sources.fn(service(restrictions_source(), gated_source()))
    by_id = {item.id: item for item in published}
    assert by_id["approved_restrictions"].kind == "enumerable"
    assert by_id["approved_restrictions"].requires_authentication is False
    assert by_id["builds_pending"].requires_authentication is True
    assert by_id["builds_pending"].value_type == "integer"
