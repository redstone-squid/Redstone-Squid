"""Attachment routes use the same draft precondition and command as Discord history."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squid.api.errors import register_exception_handlers
from squid.api.idempotency import edit_operation_id, reserve_edit_request
from squid.api.revisions import revision_etag
from squid.api.v1.submission_intake import intake_service
from squid.api.v1.submission_intake import router as intake_router
from squid.api.v1.submission_schematics import router as schematics_router
from squid.api.v1.submission_schematics import schematic_service
from squid.api.v1.submissions import authenticated_account, get_submission_edits
from squid.edits.domain import Revision
from squid.submissions.application.attachment_edits import DRAFT_ATTACHMENTS, AttachmentValues
from squid.submissions.application.edits import draft_target


@pytest.mark.parametrize("route", ["schematics", "schematics/primary", "supplied-files"])
def test_attachment_routes_require_displayed_revision_and_return_the_committed_etag(route: str) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(schematics_router)
    app.include_router(intake_router)
    draft_id, source_id, operation_id = uuid4(), uuid4(), uuid4()
    target = draft_target(draft_id)
    runner = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(outcome=SimpleNamespace(revisions=(Revision(target, 5),))))
    )
    queries = SimpleNamespace(list=AsyncMock(return_value=[]))
    app.dependency_overrides[authenticated_account] = lambda: 7
    app.dependency_overrides[get_submission_edits] = lambda: runner
    app.dependency_overrides[schematic_service] = lambda: queries
    app.dependency_overrides[intake_service] = lambda: queries
    app.dependency_overrides[reserve_edit_request] = lambda: None
    app.dependency_overrides[edit_operation_id] = lambda: operation_id
    primary = route.endswith("/primary")
    path = f"/submissions/drafts/{draft_id}/{route.split('/')[0]}/{source_id}" + ("/primary" if primary else "")
    with TestClient(app) as client:
        method = client.post if primary else client.delete
        assert method(path).status_code == 428
        assert (
            method(path, headers={"If-Match": revision_etag(Revision(draft_target(UUID(int=1)), 4))}).status_code == 412
        )
        runner.execute.assert_not_awaited()
        response = method(path, headers={"If-Match": revision_etag(Revision(target, 4))})
    assert response.status_code == 200
    assert response.headers["ETag"] == revision_etag(Revision(target, 5))
    command, actor = runner.execute.call_args.args
    assert command.kind == DRAFT_ATTACHMENTS
    assert command.operation_id == operation_id
    assert command.expected == (Revision(target, 4),)
    assert actor.account_id == 7
    values = AttachmentValues.model_validate_json(command.values.encoded)
    if primary:
        assert values.select_primary
        assert values.primary == source_id
    else:
        assert values.files[0].source_id == source_id
        assert not values.files[0].present
