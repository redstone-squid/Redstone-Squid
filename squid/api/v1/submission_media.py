"""Upload draft images and videos, then expose their processing status.

The upload route checks draft ownership before streaming bytes to a private
temporary file. Registration retains the source for background normalization;
the response reports the job's state, not a published media URL. These uploads
enforce their own byte limits because they bypass the general body buffer.
"""

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

import anyio.to_thread
from litestar import Request, Response, Router, delete, get, post
from litestar import status_codes as status
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery, QueryParameter, SkipValidation

from squid.api.attachment_edits import AttachmentEdit, attachment_edit_request
from squid.api.contract import DEVICE, MINECRAFT, WEB, WEB_WRITE, cli_command, contract
from squid.api.dependencies import DraftAttachments, get_draft_attachments
from squid.api.errors import responses
from squid.api.idempotency import edit_operation_id, reserve_edit_request, reserve_idempotent_request
from squid.api.request_body import streams_own_body
from squid.api.v1.schemas.submission_media import (
    DraftMediaLimitsResponse,
    DraftMediaListResponse,
    DraftMediaResponse,
)
from squid.api.v1.submissions import authenticated_account, get_submission_edits
from squid.core.actors import Actor
from squid.media.domain import MediaKind, MediaLimitMeasure, MediaLimits, MediaViolation
from squid.media.errors import DraftMediaRequestError, MediaLimitExceededError
from squid.submissions.application import StagedUpload
from squid.submissions.application.attachment_edits import AttachmentValues, FileMembership

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

logger = logging.getLogger(__name__)


AccountId = NamedDependency[SkipValidation[int]]
StripAudio = Annotated[bool, QueryParameter(description="Remove audio while normalizing a video.")]
UploadId = Annotated[UUID | None, QueryParameter(description="Stable client-generated UUID for safe upload retries.")]


@post(
    "/{kind:str}",
    status_code=status.HTTP_202_ACCEPTED,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    operation_id="submission_media_upload",
    opt={
        "requestBody": _STREAMING_REQUEST_BODY,
        **contract(
            cli=cli_command("media.upload", features=("submission-media",), interaction="direct"),
        ),
    },
    dependencies={"account_id": Provide(authenticated_account)},
)
@streams_own_body
async def upload_draft_media(
    draft_id: FromPath[UUID],
    kind: FromPath[MediaKind],
    request: Request,
    attachments: DraftAttachments,
    account_id: AccountId,
    strip_audio: StripAudio = False,
    upload_id: UploadId = None,
) -> Response[DraftMediaResponse]:
    """Stream one owned-draft upload through a private bounded staging file."""
    response_headers: dict[str, str] = {}
    _require_non_nil(draft_id, reason="nil_draft_id")
    if upload_id is not None:
        _require_non_nil(upload_id, reason="nil_upload_id")
    authority = await attachments.authorize_upload(draft_id, Actor(account_id=account_id), kind)
    _require_query_names(request, _UPLOAD_QUERY_NAMES)
    if kind is MediaKind.IMAGE and strip_audio:
        reason = "strip_audio_requires_video"
        raise DraftMediaRequestError(reason)
    content_type = _source_content_type(request, kind)
    content_length = _declared_content_length(request, attachments.limits.max_source_bytes)
    with _private_upload_directory() as directory:
        source_path = directory / "source"
        await _stream_to_private_file(
            request, source_path, content_length=content_length, max_bytes=attachments.limits.max_source_bytes
        )
        snapshot = await attachments.register(
            authority, StagedUpload(source_path, content_type), strip_audio=strip_audio, upload_id=upload_id
        )
    _prevent_storage(response_headers, attachments.limits)
    return Response(
        content=DraftMediaResponse.from_snapshot(snapshot),
        headers=response_headers,
        status_code=status.HTTP_202_ACCEPTED,
    )


@contextmanager
def _private_upload_directory() -> Iterator[Path]:
    """Create a private staging directory, then remove it on exit and log cleanup failures."""
    directory = Path(tempfile.mkdtemp(prefix="squid-media-upload-"))
    try:
        directory.chmod(0o700)
        yield directory
    finally:
        try:
            shutil.rmtree(directory)
        except OSError:
            logger.warning("Unable to remove a private upload staging directory", exc_info=True)


@get(
    "/",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="submission_media_list",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("media.list", features=("submission-media",), interaction="direct"),
    ),
    status_code=200,
    dependencies={"account_id": Provide(authenticated_account)},
)
async def list_draft_media(
    draft_id: FromPath[UUID], request: Request, attachments: DraftAttachments, account_id: AccountId
) -> Response[DraftMediaListResponse]:
    """List all retained states and safe normalized facts for an owned draft."""
    response_headers: dict[str, str] = {}
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    snapshots = await attachments.list(draft_id, Actor(account_id=account_id))
    _prevent_storage(response_headers, attachments.limits)
    return Response(
        content=DraftMediaListResponse(
            limits=DraftMediaLimitsResponse.from_domain(attachments.limits),
            media=[DraftMediaResponse.from_snapshot(snapshot) for snapshot in snapshots],
        ),
        headers=response_headers,
        status_code=200,
    )


@get(
    "/{upload_id:uuid}",
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="submission_media_get",
    security=[WEB, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("media.status", features=("submission-media",), interaction="direct"),
    ),
    status_code=200,
    dependencies={"account_id": Provide(authenticated_account)},
)
async def get_draft_media(
    draft_id: FromPath[UUID],
    upload_id: FromPath[UUID],
    request: Request,
    attachments: DraftAttachments,
    account_id: AccountId,
) -> Response[DraftMediaResponse]:
    """Return one upload after enforcing both draft ownership and association."""
    response_headers: dict[str, str] = {}
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    _require_non_nil(upload_id, reason="nil_upload_id")
    snapshot = await attachments.get(draft_id, Actor(account_id=account_id), upload_id)
    _prevent_storage(response_headers, attachments.limits)
    return Response(content=DraftMediaResponse.from_snapshot(snapshot), headers=response_headers, status_code=200)


@delete(
    "/{upload_id:uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
    operation_id="submission_media_discard",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("media.discard", features=("submission-media",), interaction="direct"),
    ),
    dependencies={
        "_reservation": Provide(reserve_edit_request),
        "account_id": Provide(authenticated_account),
        "edit": Provide(attachment_edit_request, sync_to_thread=False),
        "runner": Provide(get_submission_edits, sync_to_thread=False),
        "operation_id": Provide(edit_operation_id, sync_to_thread=False),
    },
)
async def discard_draft_media(
    draft_id: FromPath[UUID],
    upload_id: FromPath[UUID],
    request: Request,
    attachments: DraftAttachments,
    account_id: AccountId,
    edit: AttachmentEdit,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> Response[None]:
    """Withdraw one upload while retaining a stable discarded state."""
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    _require_non_nil(upload_id, reason="nil_upload_id")
    response = Response(
        content=None,
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": _NO_STORE, _MAX_UPLOAD_HEADER: str(attachments.limits.max_source_bytes)},
    )
    response.headers["ETag"] = await edit.save(
        AttachmentValues(files=(FileMembership(source_id=upload_id, present=False),))
    )
    return response


def _source_content_type(request: Request, kind: MediaKind) -> str:
    """Require one ASCII media type matching the upload kind, with no parameters."""
    values = _raw_header_values(request, b"content-type")
    if len(values) != 1:
        reason = "content_type_required_once"
        raise DraftMediaRequestError(reason)
    try:
        content_type = values[0].decode("ascii").lower()
    except UnicodeDecodeError:
        raise DraftMediaRequestError(_CONTENT_TYPE_INVALID) from None
    if not _MEDIA_TYPE.fullmatch(content_type):
        raise DraftMediaRequestError(_CONTENT_TYPE_INVALID)
    if not content_type.startswith(f"{kind.value}/"):
        reason = "content_type_kind_mismatch"
        raise DraftMediaRequestError(reason)
    return content_type


def _declared_content_length(request: Request, max_bytes: int) -> int:
    """Require one positive Content-Length within the upload limit.

    Transfer-Encoding is rejected, and Content-Encoding may only be identity.
    Compare the decimal text to the limit before converting it to an integer.
    """
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
    limit = str(max_bytes)
    if len(raw) > len(limit) or (len(raw) == len(limit) and raw > limit):
        # The public limit response does not expose the attacker-controlled value. Use the
        # smallest proven violation rather than materializing an unbounded decimal integer.
        content_length = max_bytes + 1
        raise MediaLimitExceededError(MediaViolation(MediaLimitMeasure.SOURCE_BYTES, content_length, max_bytes))
    return int(raw)


async def _stream_to_private_file(
    request: Request,
    destination: Path,
    *,
    content_length: int,
    max_bytes: int,
) -> None:
    """Create a private file and stream exactly content_length bytes into it.

    Reject excess or missing bytes and enforce max_bytes independently. Wait for
    each thread write to finish before closing the descriptor, even if cancelled.
    The caller owns cleanup of the file, including partial writes on failure.
    """
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
                await anyio.to_thread.run_sync(_write_all, descriptor, chunk, abandon_on_cancel=False)
        if received != content_length:
            raise DraftMediaRequestError(_CONTENT_LENGTH_MISMATCH)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    """Write the complete buffer, retrying partial OS writes."""
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


def _prevent_storage(headers: dict[str, str], limits: MediaLimits) -> None:
    headers["Cache-Control"] = _NO_STORE
    headers[_MAX_UPLOAD_HEADER] = str(limits.max_source_bytes)


@post(
    "/{upload_id:uuid}/attach",
    operation_id="submission_media_attach",
    security=[WEB_WRITE, DEVICE, MINECRAFT],
    opt=contract(
        cli=cli_command("media.attach", features=("submission-media",), interaction="direct"),
    ),
    status_code=200,
    dependencies={
        "_reservation": Provide(reserve_idempotent_request),
        "account_id": Provide(authenticated_account),
    },
)
async def attach_draft_media(
    draft_id: FromPath[UUID],
    upload_id: FromPath[UUID],
    source_draft_id: FromQuery[UUID],
    attachments: DraftAttachments,
    account_id: AccountId,
    *,
    _reservation: NamedDependency[SkipValidation[None]],
) -> DraftMediaResponse:
    """Reference a file supplied to another owned draft without duplicating its processing."""
    return DraftMediaResponse.from_snapshot(
        await attachments.attach(source_draft_id, draft_id, Actor(account_id=account_id), upload_id)
    )


router = Router(
    dependencies={"attachments": Provide(get_draft_attachments)},
    path="/submissions/drafts/{draft_id:uuid}/media",
    tags=["submissions"],
    route_handlers=[upload_draft_media, list_draft_media, get_draft_media, discard_draft_media, attach_draft_media],
)
