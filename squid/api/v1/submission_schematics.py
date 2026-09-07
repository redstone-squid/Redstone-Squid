"""Private schematic intake and explicit per-draft file selection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from squid.api.contract import DEVICE, MINECRAFT, WEB, WEB_WRITE, contract, transport_only
from squid.api.idempotency import enforce_request_idempotency
from squid.api.request_body import streams_own_body
from squid.api.v1.submissions import AccountId
from squid.core.errors import ValidationError
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState


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


Schematics = Annotated[DraftSchematicService, Depends(schematic_service)]
router = APIRouter(prefix="/submissions/drafts/{draft_id}/schematics", tags=["submissions"])
_READ = contract(security=[WEB, DEVICE, MINECRAFT], cli=transport_only())
_WRITE = contract(security=[WEB_WRITE, DEVICE, MINECRAFT], cli=transport_only())


def _check_size(size: int, maximum: int) -> None:
    if size > maximum:
        message = "Schematic exceeds the upload limit."
        raise ValidationError(message)


def _response(source: DraftSchematic) -> DraftSchematicResponse:
    return DraftSchematicResponse.model_validate(source)


@router.get("", operation_id="submission_schematic_list", openapi_extra=_READ)
async def list_schematics(
    draft_id: UUID, account_id: AccountId, schematics: Schematics
) -> list[DraftSchematicResponse]:
    """List retained sources, including failures and explicit discards."""
    return [_response(source) for source in await schematics.list(draft_id, account_id)]


@router.put(
    "/{upload_id}",
    operation_id="submission_schematic_upload",
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
async def upload_schematic(
    draft_id: UUID,
    upload_id: UUID,
    request: Request,
    account_id: AccountId,
    schematics: Schematics,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
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
    return _response(source)


@router.post(
    "/{upload_id}/primary",
    operation_id="submission_schematic_primary",
    openapi_extra=_WRITE,
    dependencies=[Depends(enforce_request_idempotency)],
)
async def select_primary(
    draft_id: UUID, upload_id: UUID, account_id: AccountId, schematics: Schematics
) -> list[DraftSchematicResponse]:
    """Explicitly select the primary schematic for this draft."""
    await schematics.select_primary(draft_id, account_id, upload_id)
    return await list_schematics(draft_id, account_id, schematics)


@router.delete(
    "/{upload_id}",
    operation_id="submission_schematic_discard",
    openapi_extra=_WRITE,
    dependencies=[Depends(enforce_request_idempotency)],
)
async def discard_schematic(
    draft_id: UUID, upload_id: UUID, account_id: AccountId, schematics: Schematics
) -> list[DraftSchematicResponse]:
    """Discard one draft reference; other candidates keep their source."""
    await schematics.discard(draft_id, account_id, upload_id)
    return await list_schematics(draft_id, account_id, schematics)


@router.post(
    "/{upload_id}/attach",
    operation_id="submission_schematic_attach",
    openapi_extra=_WRITE,
    dependencies=[Depends(enforce_request_idempotency)],
)
async def attach_schematic(
    draft_id: UUID,
    upload_id: UUID,
    payload: AttachSchematicRequest,
    account_id: AccountId,
    schematics: Schematics,
) -> list[DraftSchematicResponse]:
    """Assign an already supplied file to another candidate without another upload."""
    await schematics.attach(payload.source_draft_id, draft_id, account_id, upload_id)
    return await list_schematics(draft_id, account_id, schematics)
