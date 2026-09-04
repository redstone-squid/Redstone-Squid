"""Feature-state acceptance tests for submission media routes."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from squid.api.errors import register_exception_handlers
from squid.api.v1.submission_media import router
from squid.api.v1.submissions import authenticated_account
from tests.unit.submissions.media_api_fakes import (
    ACCOUNT_ID,
    DRAFT_ID,
    DisabledMediaRuntime,
    DisabledMediaServices,
    FakeDrafts,
)

pytestmark = pytest.mark.asyncio


async def test_disabled_media_service_fails_closed_with_service_unavailable() -> None:
    events: list[str] = []
    drafts = FakeDrafts(events)
    app = FastAPI()
    app.state.runtime = DisabledMediaRuntime(DisabledMediaServices(media_jobs=None, submission_drafts=drafts))
    register_exception_handlers(app)
    app.include_router(router)

    async def account_dependency() -> int:
        return ACCOUNT_ID

    app.dependency_overrides[authenticated_account] = account_dependency
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/submissions/drafts/{DRAFT_ID}/media")

    assert response.status_code == 503
    assert response.json()["resource"] == "submission_media"
    assert events == []
