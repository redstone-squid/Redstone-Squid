"""The private suggestion route exposes its own response rather than the upload DTO."""

from uuid import uuid4

from httpx import AsyncClient
from pytest_mock import MockerFixture

from squid.api.v1.submission_schematics import router, schematic_service
from squid.api.v1.submissions import authenticated_account
from squid.core.submissions.application.schematic_inference import SchematicDetection, SchematicSubmissionSuggestions
from squid.core.submissions.domain.schematics import DraftSchematicState
from tests.support.litestar import asgi_transport, route_app


async def test_suggestion_response_includes_explicit_detection_failures(mocker: MockerFixture) -> None:
    draft_id, upload_id = uuid4(), uuid4()
    service = mocker.Mock()
    service.suggestions = mocker.AsyncMock(
        return_value=SchematicSubmissionSuggestions(
            upload_id, DraftSchematicState.READY, 5, detections=(SchematicDetection("category"),)
        )
    )
    app = route_app([router], overrides={schematic_service: lambda: service, authenticated_account: lambda: 42})
    async with AsyncClient(transport=asgi_transport(app), base_url="http://test") as client:
        response = await client.get(f"/submissions/drafts/{draft_id}/schematics/{upload_id}/suggestions")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["draft_revision"] == 5
    assert payload["upload_id"] == str(upload_id)
    assert payload["detections"][0]["status"] == "not_detected"
    assert payload["detections"][0]["value"] is None
    assert service.suggestions.call_args.args[1].account_id == 42
