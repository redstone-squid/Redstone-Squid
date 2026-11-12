"""Track files a draft still needs before it can be finalized.

Each supplied-file requirement has a source ID that remains visible while the
file is missing, registered, or explicitly discarded. Replacing a file registers
the new source before discarding the old requirement, so a failed upload leaves
the outstanding work visible to every client editing the draft.
"""

import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from squid.api.attachment_edits import AttachmentEdit
from squid.api.contract import DEVICE, MINECRAFT, WEB, WEB_WRITE, contract, transport_only
from squid.api.idempotency import reserve_edit_request
from squid.api.request_body import streams_own_body
from squid.api.v1.submissions import AccountId
from squid.core.errors import ValidationError
from squid.submissions.application.attachment_edits import AttachmentValues, FileMembership
from squid.submissions.application.intake import SubmissionAttachmentIntake
from squid.submissions.domain.intake import IntakeStatus


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
    """Register a supplied file, then discard the requirement it replaces if requested.

    Use a new source_id when replacing a file. The previous requirement is kept
    until the new upload has been registered successfully.
    """
    if replaces == source_id:
        message = "A replacement requires a new source identifier."
        raise ValidationError(message)
    content_type = request.headers.get("content-type")
    await intake.reserve(draft_id, account_id, source_id, filename, content_type)
    try:
        with tempfile.TemporaryDirectory(prefix="squid-api-intake-") as directory:
            path = Path(directory) / "source"
            await _receive(request, path, max_bytes=intake.max_file_bytes(filename, content_type))
            await intake.register_file(draft_id, account_id, source_id, path, content_type)
        if replaces is not None:
            await intake.discard(draft_id, account_id, replaces)
    except Exception:
        await intake.fail(draft_id, account_id, source_id)
        raise
    return await list_files(draft_id, account_id, intake)


async def _receive(request: Request, path: Path, *, max_bytes: int) -> None:
    """Stream a supplied file to disk within the size limit and a 60-second timeout."""
    size = 0
    with anyio.fail_after(60), path.open("wb") as output:
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes:
                message = f"This file exceeds the upload limit of {max_bytes} bytes."
                raise ValidationError(message)
            output.write(chunk)


@router.delete(
    "/{source_id}",
    operation_id="submission_supplied_file_discard",
    openapi_extra=_WRITE,
    dependencies=[Depends(reserve_edit_request)],
)
async def discard_file(
    draft_id: UUID, source_id: UUID, account_id: AccountId, intake: Intake, edit: AttachmentEdit, response: Response
) -> list[SuppliedFileResponse]:
    """Explicitly resolve a file that does not belong to this candidate."""
    await edit.save(AttachmentValues(files=(FileMembership(source_id=source_id, present=False),)), response)
    return await list_files(draft_id, account_id, intake)
