"""Strict streaming HTTP routes for account-owned draft media."""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from squid.api.contract import DEVICE, MINECRAFT, WEB, WEB_WRITE, cli_command, contract
from squid.api.dependencies import DraftAttachments
from squid.api.errors import responses
from squid.api.idempotency import enforce_request_idempotency
from squid.api.request_body import streams_own_body
from squid.api.v1.schemas.submission_media import (
    DraftMediaLimitsResponse,
    DraftMediaListResponse,
    DraftMediaResponse,
)
from squid.api.v1.submissions import authenticated_account
from squid.media.domain import MediaKind, MediaLimitMeasure, MediaLimits, MediaViolation
from squid.media.errors import DraftMediaRequestError, MediaLimitExceededError
from squid.submissions.application import StagedUpload

_NO_STORE = "no-store"
_MAX_UPLOAD_HEADER = "Squid-Max-Upload-Bytes"
_MEDIA_TYPE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CONTENT_LENGTH = re.compile(r"^[1-9][0-9]*$")
_UPLOAD_QUERY_NAMES = frozenset({"strip_audio", "upload_id"})
_CONTENT_ENCODING_UNSUPPORTED = "content_encoding_not_supported"
_CONTENT_LENGTH_INVALID = "content_length_invalid"
_CONTENT_LENGTH_MISMATCH = "content_length_mismatch"
_CONTENT_TYPE_INVALID = "content_type_invalid"
_STREAMING_REQUEST_BODY = {
    "required": True,
    "content": {
        "image/*": {"schema": {"type": "string", "format": "binary"}},
        "video/*": {"schema": {"type": "string", "format": "binary"}},
    },
}


AccountId = Annotated[int, Depends(authenticated_account)]
StripAudio = Annotated[bool, Query(description="Remove audio while normalizing a video.")]
UploadId = Annotated[UUID | None, Query(description="Stable client-generated UUID for safe upload retries.")]

router = APIRouter(prefix="/submissions/drafts/{draft_id}/media", tags=["submissions"])


@router.post(
    "/{kind}",
    response_model=DraftMediaResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    operation_id="submission_media_upload",
    openapi_extra={
        "requestBody": _STREAMING_REQUEST_BODY,
        **contract(
            security=[WEB_WRITE, DEVICE, MINECRAFT],
            cli=cli_command("media.upload", features=("submission-media",), interaction="direct"),
        ),
    },
)
@streams_own_body
async def upload_draft_media(
    draft_id: UUID,
    kind: MediaKind,
    request: Request,
    response: Response,
    attachments: DraftAttachments,
    account_id: AccountId,
    strip_audio: StripAudio = False,
    upload_id: UploadId = None,
) -> DraftMediaResponse:
    """Stream one owned-draft upload through a private bounded staging file."""
    authority = await attachments.authorize_upload(draft_id, account_id, kind)
    _require_query_names(request, _UPLOAD_QUERY_NAMES)
    _require_non_nil(draft_id, reason="nil_draft_id")
    if upload_id is not None:
        _require_non_nil(upload_id, reason="nil_upload_id")
    if kind is MediaKind.IMAGE and strip_audio:
        reason = "strip_audio_requires_video"
        raise DraftMediaRequestError(reason)

    content_type = _source_content_type(request, kind)
    content_length = _declared_content_length(request, attachments.limits.max_source_bytes)
    with tempfile.TemporaryDirectory(prefix="squid-media-upload-") as directory_name:
        directory = Path(directory_name)
        directory.chmod(0o700)
        source_path = directory / "source"
        await _stream_to_private_file(
            request,
            source_path,
            content_length=content_length,
            max_bytes=attachments.limits.max_source_bytes,
        )
        snapshot = await attachments.register(
            authority,
            StagedUpload(source_path, content_type),
            strip_audio=strip_audio,
            upload_id=upload_id,
        )

    _prevent_storage(response, attachments.limits)
    return DraftMediaResponse.from_snapshot(snapshot)


@router.get(
    "",
    response_model=DraftMediaListResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="submission_media_list",
    openapi_extra=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("media.list", features=("submission-media",), interaction="direct"),
    ),
)
async def list_draft_media(
    draft_id: UUID,
    request: Request,
    response: Response,
    attachments: DraftAttachments,
    account_id: AccountId,
) -> DraftMediaListResponse:
    """List all retained states and safe normalized facts for an owned draft."""
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    snapshots = await attachments.list(draft_id, account_id)
    _prevent_storage(response, attachments.limits)
    return DraftMediaListResponse(
        limits=DraftMediaLimitsResponse.from_domain(attachments.limits),
        media=[DraftMediaResponse.from_snapshot(snapshot) for snapshot in snapshots],
    )


@router.get(
    "/{upload_id}",
    response_model=DraftMediaResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="submission_media_get",
    openapi_extra=contract(
        security=[WEB, DEVICE, MINECRAFT],
        cli=cli_command("media.status", features=("submission-media",), interaction="direct"),
    ),
)
async def get_draft_media(
    draft_id: UUID,
    upload_id: UUID,
    request: Request,
    response: Response,
    attachments: DraftAttachments,
    account_id: AccountId,
) -> DraftMediaResponse:
    """Return one upload after enforcing both draft ownership and association."""
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    _require_non_nil(upload_id, reason="nil_upload_id")
    snapshot = await attachments.get(draft_id, account_id, upload_id)
    _prevent_storage(response, attachments.limits)
    return DraftMediaResponse.from_snapshot(snapshot)


@router.delete(
    "/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    dependencies=[Depends(enforce_request_idempotency)],
    operation_id="submission_media_discard",
    openapi_extra=contract(
        security=[WEB_WRITE, DEVICE, MINECRAFT],
        cli=cli_command("media.discard", features=("submission-media",), interaction="direct"),
    ),
)
async def discard_draft_media(
    draft_id: UUID,
    upload_id: UUID,
    request: Request,
    attachments: DraftAttachments,
    account_id: AccountId,
) -> Response:
    """Withdraw one upload while retaining a stable discarded state."""
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    _require_non_nil(upload_id, reason="nil_upload_id")
    await attachments.discard(draft_id, account_id, upload_id)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": _NO_STORE, _MAX_UPLOAD_HEADER: str(attachments.limits.max_source_bytes)},
    )


def _source_content_type(request: Request, kind: MediaKind) -> str:
    values = _raw_header_values(request, b"content-type")
    if len(values) != 1:
        reason = "content_type_required_once"
        raise DraftMediaRequestError(reason)
    try:
        content_type = values[0].decode("ascii").lower()
    except UnicodeDecodeError:
        raise DraftMediaRequestError(_CONTENT_TYPE_INVALID) from None
    if not _MEDIA_TYPE.fullmatch(content_type) or not content_type.startswith(f"{kind.value}/"):
        reason = "content_type_kind_mismatch"
        raise DraftMediaRequestError(reason)
    return content_type


def _declared_content_length(request: Request, max_bytes: int) -> int:
    if _raw_header_values(request, b"transfer-encoding"):
        reason = "transfer_encoding_not_supported"
        raise DraftMediaRequestError(reason)
    if values := _raw_header_values(request, b"content-encoding"):
        try:
            encodings = {value.decode("ascii").lower() for value in values}
        except UnicodeDecodeError:
            raise DraftMediaRequestError(_CONTENT_ENCODING_UNSUPPORTED) from None
        if encodings != {"identity"}:
            raise DraftMediaRequestError(_CONTENT_ENCODING_UNSUPPORTED)
    values = _raw_header_values(request, b"content-length")
    if len(values) != 1:
        reason = "content_length_required_once"
        raise DraftMediaRequestError(reason)
    try:
        raw = values[0].decode("ascii")
    except UnicodeDecodeError:
        raise DraftMediaRequestError(_CONTENT_LENGTH_INVALID) from None
    if not _CONTENT_LENGTH.fullmatch(raw):
        raise DraftMediaRequestError(_CONTENT_LENGTH_INVALID)
    content_length = int(raw)
    if content_length > max_bytes:
        raise MediaLimitExceededError(MediaViolation(MediaLimitMeasure.SOURCE_BYTES, content_length, max_bytes))
    return content_length


async def _stream_to_private_file(
    request: Request,
    destination: Path,
    *,
    content_length: int,
    max_bytes: int,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    received = 0
    try:
        os.fchmod(descriptor, 0o600)
        async for chunk in request.stream():
            received += len(chunk)
            if received > content_length:
                raise DraftMediaRequestError(_CONTENT_LENGTH_MISMATCH)
            if received > max_bytes:
                raise MediaLimitExceededError(MediaViolation(MediaLimitMeasure.SOURCE_BYTES, received, max_bytes))
            if chunk:
                await asyncio.to_thread(_write_all, descriptor, chunk)
        if received != content_length:
            raise DraftMediaRequestError(_CONTENT_LENGTH_MISMATCH)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            msg = "Unable to stage the media upload."
            raise OSError(msg)
        remaining = remaining[written:]


def _raw_header_values(request: Request, name: bytes) -> list[bytes]:
    return [value for header_name, value in request.scope.get("headers", ()) if header_name.lower() == name]


def _require_query_names(request: Request, allowed: frozenset[str]) -> None:
    names = [name for name, _value in request.query_params.multi_items()]
    if any(name not in allowed for name in names) or len(names) != len(set(names)):
        reason = "query_parameters_invalid"
        raise DraftMediaRequestError(reason)


def _require_non_nil(identifier: UUID, *, reason: str) -> None:
    if identifier.int == 0:
        raise DraftMediaRequestError(reason)


def _prevent_storage(response: Response, limits: MediaLimits) -> None:
    response.headers["Cache-Control"] = _NO_STORE
    response.headers[_MAX_UPLOAD_HEADER] = str(limits.max_source_bytes)
