"""Private schematic intake and explicit per-draft file selection."""

from typing import Annotated
from uuid import UUID

from litestar import Request, Response, Router, delete, get, post, put
from litestar.di import NamedDependency, Provide
from litestar.params import JSONBody, QueryParameter, SkipValidation
from pydantic import BaseModel, ConfigDict

from squid.api.attachment_edits import AttachmentEdit, attachment_edit_request
from squid.api.contract import DEVICE, MINECRAFT, WEB, WEB_WRITE, contract, transport_only
from squid.api.idempotency import edit_operation_id, reserve_edit_request, reserve_idempotent_request
from squid.api.request_body import streams_own_body
from squid.api.v1.submissions import AccountId, authenticated_account, get_submission_edits
from squid.core.errors import ValidationError
from squid.submissions.application.attachment_edits import AttachmentValues, FileMembership
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.domain.schematics import DraftSchematicState


class DraftSchematicResponse(BaseModel):
    """Private source metadata without an object key or download URL."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    sha256: str | None
    byte_size: int | None
    state: DraftSchematicState
    primary: bool
    discarded: bool


class AttachSchematicRequest(BaseModel):
    """Reference a file already supplied to another draft owned by the same account."""

    source_draft_id: UUID


def schematic_service(request: Request) -> DraftSchematicService:
    """Resolve the application service from the API runtime."""
    return request.app.state.runtime.services.submission_schematics


Schematics = NamedDependency[SkipValidation[DraftSchematicService]]
_READ = contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only())
_WRITE = contract(security=[WEB_WRITE, DEVICE, MINECRAFT], cli=transport_only())


def _check_size(size: int, maximum: int) -> None:
    if size > maximum:
        message = "Schematic exceeds the upload limit."
        raise ValidationError(message)


@get(
    "/",
    operation_id="submission_schematic_list",
    opt=_READ,
    status_code=200,
    dependencies={
        "account_id": Provide(authenticated_account),
        "schematics": Provide(schematic_service, sync_to_thread=False),
    },
)
async def list_schematics(
    draft_id: UUID, account_id: AccountId, schematics: Schematics
) -> list[DraftSchematicResponse]:
    """List retained sources, including failures and explicit discards."""
    return [DraftSchematicResponse.model_validate(source) for source in await schematics.list(draft_id, account_id)]


@put(
    "/{upload_id:uuid}",
    operation_id="submission_schematic_upload",
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
        "schematics": Provide(schematic_service, sync_to_thread=False),
    },
)
@streams_own_body
async def upload_schematic(
    draft_id: UUID,
    upload_id: UUID,
    request: Request,
    account_id: AccountId,
    schematics: Schematics,
    filename: Annotated[str, QueryParameter(min_length=1, max_length=255)],
) -> DraftSchematicResponse:
    """Upload or retry one stable source ID while keeping its bytes private."""
    await schematics.reserve(draft_id, account_id, filename=filename, upload_id=upload_id)
    data = bytearray()
    try:
        async for chunk in request.stream():
            _check_size(len(data) + len(chunk), schematics.max_bytes)
            data.extend(chunk)
        source = await schematics.upload(draft_id, account_id, filename=filename, data=bytes(data), upload_id=upload_id)
    except Exception:
        await schematics.fail(draft_id, account_id, upload_id)
        raise
    return DraftSchematicResponse.model_validate(source)


@post(
    "/{upload_id:uuid}/primary",
    operation_id="submission_schematic_primary",
    opt=_WRITE,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "account_id": Provide(authenticated_account),
        "schematics": Provide(schematic_service, sync_to_thread=False),
        "edit": Provide(attachment_edit_request, sync_to_thread=False),
        "runner": Provide(get_submission_edits, sync_to_thread=False),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def select_primary(
    draft_id: UUID,
    upload_id: UUID,
    account_id: AccountId,
    schematics: Schematics,
    edit: AttachmentEdit,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[list[DraftSchematicResponse]]:
    """Explicitly select the primary schematic for this draft."""
    response_headers: dict[str, str] = {}
    response_headers["ETag"] = await edit.save(AttachmentValues(primary=upload_id, select_primary=True))
    return Response(
        content=await list_schematics.fn(draft_id, account_id, schematics), headers=response_headers, status_code=200
    )


@delete(
    "/{upload_id:uuid}",
    operation_id="submission_schematic_discard",
    opt=_WRITE,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "account_id": Provide(authenticated_account),
        "schematics": Provide(schematic_service, sync_to_thread=False),
        "edit": Provide(attachment_edit_request, sync_to_thread=False),
        "runner": Provide(get_submission_edits, sync_to_thread=False),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def discard_schematic(
    draft_id: UUID,
    upload_id: UUID,
    account_id: AccountId,
    schematics: Schematics,
    edit: AttachmentEdit,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[list[DraftSchematicResponse]]:
    """Discard one draft reference; other candidates keep their source."""
    response_headers: dict[str, str] = {}
    response_headers["ETag"] = await edit.save(
        AttachmentValues(files=(FileMembership(source_id=upload_id, present=False),))
    )
    return Response(
        content=await list_schematics.fn(draft_id, account_id, schematics), headers=response_headers, status_code=200
    )


@post(
    "/{upload_id:uuid}/attach",
    operation_id="submission_schematic_attach",
    opt=_WRITE,
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "account_id": Provide(authenticated_account),
        "schematics": Provide(schematic_service, sync_to_thread=False),
    },
)
async def attach_schematic(
    draft_id: UUID,
    upload_id: UUID,
    data: JSONBody[AttachSchematicRequest],
    account_id: AccountId,
    schematics: Schematics,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> list[DraftSchematicResponse]:
    """Assign an already supplied file to another candidate without another upload."""
    await schematics.attach(data.source_draft_id, draft_id, account_id, upload_id)
    return await list_schematics.fn(draft_id, account_id, schematics)


router = Router(
    path="/submissions/drafts/{draft_id:uuid}/schematics",
    tags=["submissions"],
    route_handlers=[list_schematics, upload_schematic, select_primary, discard_schematic, attach_schematic],
)
