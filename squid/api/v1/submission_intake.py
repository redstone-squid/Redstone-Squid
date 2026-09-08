"""Cross-client resolution of retained supplied-file requirements."""

import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from squid.api.contract import DEVICE, MINECRAFT, WEB, WEB_WRITE, contract, transport_only
from squid.api.idempotency import enforce_request_idempotency
from squid.api.request_body import streams_own_body
from squid.api.v1.submissions import AccountId
from squid.core.errors import ValidationError
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.submissions.application.intake import IntakeStatus, SubmissionAttachmentIntake


class SuppliedFileResponse(BaseModel):
    """Retained requirement metadata, excluding private source URLs and storage keys."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    kind: str
    status: IntakeStatus


def intake_service(request: Request) -> SubmissionAttachmentIntake:
    return request.app.state.runtime.services.submission_intake


Intake = Annotated[SubmissionAttachmentIntake, Depends(intake_service)]
router = APIRouter(prefix="/submissions/drafts/{draft_id}/supplied-files", tags=["submissions"])
_READ = contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only())
_WRITE = contract(security=[WEB_WRITE, DEVICE, MINECRAFT], cli=transport_only())


@router.get("", operation_id="submission_supplied_file_list", openapi_extra=_READ)
async def list_files(draft_id: UUID, account_id: AccountId, intake: Intake) -> list[SuppliedFileResponse]:
    """List unresolved, registered, and explicitly discarded supplied files."""
    return [SuppliedFileResponse.model_validate(item) for item in await intake.list(draft_id, account_id)]


@router.put(
    "/{source_id}",
    operation_id="submission_supplied_file_upload",
    status_code=202,
    openapi_extra={
        **_WRITE,
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
@streams_own_body
async def upload_file(
    draft_id: UUID,
    source_id: UUID,
    request: Request,
    account_id: AccountId,
    intake: Intake,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    replaces: UUID | None = None,
) -> list[SuppliedFileResponse]:
    """Register a supplied source, or a new replacement identity, before resolving its old requirement."""
    if replaces == source_id:
        message = "A replacement requires a new source identifier."
        raise ValidationError(message)
    content_type = request.headers.get("content-type")
    await intake.reserve(draft_id, account_id, source_id, filename, content_type)
    try:
        with tempfile.TemporaryDirectory(prefix="squid-api-intake-") as directory:
            path = Path(directory) / "source"
            await _receive(request, path)
            await intake.register_file(draft_id, account_id, source_id, path, content_type)
        if replaces is not None:
            await intake.discard(draft_id, account_id, replaces)
    except Exception:
        await intake.fail(draft_id, account_id, source_id)
        raise
    return await list_files(draft_id, account_id, intake)


async def _receive(request: Request, path: Path) -> None:
    size = 0
    with anyio.fail_after(60), path.open("wb") as output:
        async for chunk in request.stream():
            size += len(chunk)
            if size > SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
                message = "Supplied files are limited to 16 MiB."
                raise ValidationError(message)
            output.write(chunk)


@router.delete(
    "/{source_id}",
    operation_id="submission_supplied_file_discard",
    openapi_extra=_WRITE,
    dependencies=[Depends(enforce_request_idempotency)],
)
async def discard_file(
    draft_id: UUID, source_id: UUID, account_id: AccountId, intake: Intake
) -> list[SuppliedFileResponse]:
    """Explicitly resolve a file that does not belong to this candidate."""
    await intake.discard(draft_id, account_id, source_id)
    return await list_files(draft_id, account_id, intake)
