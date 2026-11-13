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
from litestar import Request, Response, Router, delete, get, put
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery, QueryParameter, SkipValidation
from pydantic import BaseModel, ConfigDict

from squid.api.attachment_edits import AttachmentEdit, attachment_edit_request
from squid.api.contract import DEVICE, MINECRAFT, WEB, WEB_WRITE, contract, transport_only
from squid.api.idempotency import edit_operation_id, reserve_edit_request
from squid.api.request_body import streams_own_body
from squid.api.v1.submissions import AccountId, authenticated_account, get_submission_edits
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


Intake = NamedDependency[SkipValidation[SubmissionAttachmentIntake]]
_READ = contract(cli=transport_only())
_WRITE = contract(cli=transport_only())


@get(
    "/",
    operation_id="submission_supplied_file_list",
    security=[WEB, DEVICE, MINECRAFT],
    opt=_READ,
    status_code=200,
    dependencies={
        "account_id": Provide(authenticated_account),
        "intake": Provide(intake_service, sync_to_thread=False),
    },
)
async def list_files(draft_id: FromPath[UUID], account_id: AccountId, intake: Intake) -> list[SuppliedFileResponse]:
    """List unresolved, registered, and explicitly discarded supplied files."""
    return [SuppliedFileResponse.model_validate(item) for item in await intake.list(draft_id, account_id)]


@put(
    "/{source_id:uuid}",
    operation_id="submission_supplied_file_upload",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    status_code=202,
    opt={
        **_WRITE,
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
    dependencies={
        "account_id": Provide(authenticated_account),
        "intake": Provide(intake_service, sync_to_thread=False),
    },
)
@streams_own_body
async def upload_file(
    draft_id: FromPath[UUID],
    source_id: FromPath[UUID],
    request: Request,
    account_id: AccountId,
    intake: Intake,
    filename: Annotated[str, QueryParameter(min_length=1, max_length=255)],
    replaces: FromQuery[UUID | None] = None,
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
    return await list_files.fn(draft_id, account_id, intake)


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


@delete(
    "/{source_id:uuid}",
    operation_id="submission_supplied_file_discard",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=_WRITE,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "account_id": Provide(authenticated_account),
        "intake": Provide(intake_service, sync_to_thread=False),
        "edit": Provide(attachment_edit_request, sync_to_thread=False),
        "runner": Provide(get_submission_edits, sync_to_thread=False),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def discard_file(
    draft_id: FromPath[UUID],
    source_id: FromPath[UUID],
    account_id: AccountId,
    intake: Intake,
    edit: AttachmentEdit,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[list[SuppliedFileResponse]]:
    """Explicitly resolve a file that does not belong to this candidate."""
    response_headers: dict[str, str] = {}
    response_headers["ETag"] = await edit.save(
        AttachmentValues(files=(FileMembership(source_id=source_id, present=False),))
    )
    return Response(
        content=await list_files.fn(draft_id, account_id, intake), headers=response_headers, status_code=200
    )


router = Router(
    path="/submissions/drafts/{draft_id:uuid}/supplied-files",
    tags=["submissions"],
    route_handlers=[list_files, upload_file, discard_file],
)
