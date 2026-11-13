"""HTTP migration contracts that depend on Litestar's dependency scheduling."""

from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import UUID

import httpx
import pytest
from litestar import Litestar, get, post
from litestar.di import NamedDependency, Provide
from litestar.params import SkipValidation

from squid.api.idempotency import edit_operation_id, reserve_edit_request
from squid.api.security import ANONYMOUS
from squid.api.v1.search import search
from squid.builds.application import BuildQueryService
from squid.idempotency.application import IdempotencyService
from squid.idempotency.domain import PendingRequest
from squid.search.application import SearchService
from squid.search.domain import SearchPage, SearchScope


async def test_edit_operation_id_waits_for_dependency_reservation() -> None:
    request_id = UUID("11111111-2222-3333-4444-555555555555")
    service = create_autospec(IdempotencyService, instance=True)
    service.reserve.return_value = PendingRequest(request_id)

    @post(
        "/edit",
        status_code=200,
        dependencies={
            "caller": Provide(lambda: ANONYMOUS, sync_to_thread=False),
            "_reservation": Provide(reserve_edit_request),
            "operation_id": Provide(edit_operation_id, sync_to_thread=False),
        },
    )
    async def edit(operation_id: NamedDependency[UUID], *, _reservation: NamedDependency[SkipValidation[None]]) -> UUID:
        return operation_id

    app = Litestar([edit])
    app.state.runtime = SimpleNamespace(services=SimpleNamespace(idempotency=service))
    # Litestar annotates ASGI scopes more narrowly than httpx's mutable dictionaries.
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.post("/edit", headers={"Idempotency-Key": "edit-request"})

    assert response.status_code == 200
    assert response.json() == str(request_id)
    service.reserve.assert_awaited_once()
    assert service.reserve.await_args.kwargs["recoverable"] is True


@pytest.mark.parametrize("scope", [SearchScope.BUILDS, SearchScope.RECORDS])
async def test_search_scope_query_reaches_service_as_enum(scope: SearchScope) -> None:
    search_service = create_autospec(SearchService, instance=True)
    search_service.search.return_value = SearchPage(hits=(), total=0, next=None, prev=None)
    build_queries = create_autospec(BuildQueryService, instance=True)
    handler = get(
        "/search",
        dependencies={
            "search_service": Provide(lambda: search_service, sync_to_thread=False),
            "build_queries": Provide(lambda: build_queries, sync_to_thread=False),
        },
    )(search.fn)
    app = Litestar([handler])
    # Litestar annotates ASGI scopes more narrowly than httpx's mutable dictionaries.
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/search", params={"q": "door", "scope": scope.value})

    assert response.status_code == 200
    search_service.search.assert_awaited_once()
    assert search_service.search.await_args.args[0].scope is scope
    parameters = app.openapi_schema.to_schema()["paths"]["/search"]["get"]["parameters"]
    assert any(parameter["name"] == "scope" and parameter["in"] == "query" for parameter in parameters)
