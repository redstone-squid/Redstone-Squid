"""Strict streaming HTTP routes for account-owned draft media."""

import asyncio
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from squid.api.errors import responses
from squid.api.v1.schemas.submission_media import (
    DraftMediaLimitsResponse,
    DraftMediaListResponse,
    DraftMediaResponse,
)
from squid.api.v1.submissions import authenticated_account
from squid.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationError
from squid.media.application.jobs import (
    MediaJobSnapshot,
    MediaNormalizationJobService,
    MediaUploadConflictError,
    StagedMediaUploadSubmission,
)
from squid.media.domain import MediaKind, MediaLimitMeasure, MediaLimits, MediaViolation
from squid.media.errors import MediaLimitExceededError
from squid.submissions.application import StoredDraft

_NO_STORE = "no-store"
_MAX_UPLOAD_HEADER = "X-Squid-Max-Upload-Bytes"
_MEDIA_TYPE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CONTENT_LENGTH = re.compile(r"^[1-9][0-9]*$")
_UPLOAD_QUERY_NAMES = frozenset({"strip_audio", "upload_id"})
_CONTENT_ENCODING_UNSUPPORTED = "content_encoding_not_supported"
_CONTENT_LENGTH_INVALID = "content_length_invalid"
_CONTENT_LENGTH_MISMATCH = "content_length_mismatch"
_CONTENT_TYPE_INVALID = "content_type_invalid"


class DraftMediaJobs(Protocol):
    """Media operations needed by the owner-only HTTP transport."""

    @property
    def limits(self) -> MediaLimits: ...

    async def submit_staged(self, submission: StagedMediaUploadSubmission) -> UUID: ...

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None: ...

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]: ...

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool: ...


class DraftOwnership(Protocol):
    """Owner check required before an upload body is accepted."""

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft: ...


class SubmissionMediaApiServices(Protocol):
    """Narrow runtime bundle consumed by this isolated router."""

    media_jobs: MediaNormalizationJobService | None
    submission_drafts: DraftOwnership


class _SubmissionMediaRuntime(Protocol):
    services: SubmissionMediaApiServices


class _SubmissionMediaAppState(Protocol):
    runtime: _SubmissionMediaRuntime


class DraftMediaRequestError(ValidationError):
    """A raw upload request is ambiguous or violates its declared framing."""

    default_message = "The draft media upload request is invalid."
    default_title = "Invalid media upload"
    default_resource = "submission_media"

    def __init__(self, reason: str) -> None:
        super().__init__(public_context={"reason": reason})


class DraftMediaNotFoundError(NotFoundError):
    """No owner-visible media upload matches the requested UUID."""

    default_message = "Draft media upload not found."
    default_title = "Media upload not found"
    default_resource = "submission_media"

    def __init__(self, upload_id: UUID) -> None:
        super().__init__(public_context={"upload_id": str(upload_id)})


class DraftMediaConflictError(ConflictError):
    """A caller-provided retry UUID was already used for different bytes."""

    default_message = "The media upload identifier is already in use."
    default_title = "Media upload conflict"
    default_resource = "submission_media"

    def __init__(self, upload_id: UUID) -> None:
        super().__init__(public_context={"upload_id": str(upload_id)})


class DraftMediaUnavailableError(ServiceUnavailableError):
    """Media normalization is not enabled for this API process."""

    default_message = "Draft media processing is temporarily unavailable."
    default_resource = "submission_media"


def get_media_jobs(request: Request) -> DraftMediaJobs:
    """Resolve normalization jobs without importing the global runtime type."""
    state = cast(_SubmissionMediaAppState, request.app.state)
    media_jobs = state.runtime.services.media_jobs
    if media_jobs is None:
        raise DraftMediaUnavailableError
    return media_jobs


def get_submission_drafts(request: Request) -> DraftOwnership:
    """Resolve the shared synchronized-draft owner check."""
    state = cast(_SubmissionMediaAppState, request.app.state)
    return state.runtime.services.submission_drafts


MediaJobs = Annotated[DraftMediaJobs, Depends(get_media_jobs)]
Drafts = Annotated[DraftOwnership, Depends(get_submission_drafts)]
AccountId = Annotated[int, Depends(authenticated_account)]
StripAudio = Annotated[bool, Query(description="Remove audio while normalizing a video.")]
UploadId = Annotated[UUID | None, Query(description="Stable client-generated UUID for safe upload retries.")]

router = APIRouter(prefix="/submissions/drafts/{draft_id}/media", tags=["submissions"])


@router.post(
    "/{kind}",
    response_model=DraftMediaResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=responses(400, 401, 403, 404, 409, 422, 503),
)
async def upload_draft_media(
    draft_id: UUID,
    kind: MediaKind,
    request: Request,
    response: Response,
    media: MediaJobs,
    drafts: Drafts,
    account_id: AccountId,
    strip_audio: StripAudio = False,
    upload_id: UploadId = None,
) -> DraftMediaResponse:
    """Stream one owned-draft upload through a private bounded staging file."""
    await drafts.get_owned(draft_id, account_id)
    _require_query_names(request, _UPLOAD_QUERY_NAMES)
    _require_non_nil(draft_id, reason="nil_draft_id")
    if upload_id is not None:
        _require_non_nil(upload_id, reason="nil_upload_id")
    if kind is MediaKind.IMAGE and strip_audio:
        reason = "strip_audio_requires_video"
        raise DraftMediaRequestError(reason)

    content_type = _source_content_type(request, kind)
    content_length = _declared_content_length(request, media.limits.max_source_bytes)
    with tempfile.TemporaryDirectory(prefix="squid-media-upload-") as directory_name:
        directory = Path(directory_name)
        directory.chmod(0o700)
        source_path = directory / "source"
        await _stream_to_private_file(
            request,
            source_path,
            content_length=content_length,
            max_bytes=media.limits.max_source_bytes,
        )
        try:
            registered_id = await media.submit_staged(
                StagedMediaUploadSubmission(
                    draft_id=draft_id,
                    kind=kind,
                    source_path=source_path,
                    source_content_type=content_type,
                    strip_audio=strip_audio,
                    upload_id=upload_id,
                )
            )
        except MediaUploadConflictError as error:
            raise DraftMediaConflictError(error.upload_id) from None

    snapshot = await media.get(registered_id)
    if snapshot is None or snapshot.upload.draft_id != draft_id:
        raise DraftMediaNotFoundError(registered_id)
    _prevent_storage(response, media.limits)
    return DraftMediaResponse.from_snapshot(snapshot)


@router.get(
    "",
    response_model=DraftMediaListResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
)
async def list_draft_media(
    draft_id: UUID,
    request: Request,
    response: Response,
    media: MediaJobs,
    drafts: Drafts,
    account_id: AccountId,
) -> DraftMediaListResponse:
    """List all retained states and safe normalized facts for an owned draft."""
    await drafts.get_owned(draft_id, account_id)
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    snapshots = await media.list_for_draft(draft_id)
    _prevent_storage(response, media.limits)
    return DraftMediaListResponse(
        limits=DraftMediaLimitsResponse.from_domain(media.limits),
        media=[DraftMediaResponse.from_snapshot(snapshot) for snapshot in snapshots],
    )


@router.get(
    "/{upload_id}",
    response_model=DraftMediaResponse,
    responses=responses(400, 401, 403, 404, 422, 503),
)
async def get_draft_media(
    draft_id: UUID,
    upload_id: UUID,
    request: Request,
    response: Response,
    media: MediaJobs,
    drafts: Drafts,
    account_id: AccountId,
) -> DraftMediaResponse:
    """Return one upload after enforcing both draft ownership and association."""
    await drafts.get_owned(draft_id, account_id)
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    _require_non_nil(upload_id, reason="nil_upload_id")
    snapshot = await _owned_snapshot(media, draft_id, upload_id)
    _prevent_storage(response, media.limits)
    return DraftMediaResponse.from_snapshot(snapshot)


@router.delete(
    "/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=responses(400, 401, 403, 404, 422, 503),
)
async def discard_draft_media(
    draft_id: UUID,
    upload_id: UUID,
    request: Request,
    media: MediaJobs,
    drafts: Drafts,
    account_id: AccountId,
) -> Response:
    """Withdraw one upload while retaining a stable discarded state."""
    await drafts.get_owned(draft_id, account_id)
    _require_query_names(request, frozenset())
    _require_non_nil(draft_id, reason="nil_draft_id")
    _require_non_nil(upload_id, reason="nil_upload_id")
    if not await media.discard(draft_id, upload_id):
        raise DraftMediaNotFoundError(upload_id)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": _NO_STORE, _MAX_UPLOAD_HEADER: str(media.limits.max_source_bytes)},
    )


async def _owned_snapshot(media: DraftMediaJobs, draft_id: UUID, upload_id: UUID) -> MediaJobSnapshot:
    snapshot = await media.get(upload_id)
    if snapshot is None or snapshot.upload.draft_id != draft_id:
        raise DraftMediaNotFoundError(upload_id)
    return snapshot


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
