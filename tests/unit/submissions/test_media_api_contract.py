"""Contract tests for private streaming submission media routes."""

import asyncio
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.api.errors import register_exception_handlers
from squid.api.security import Principal, current_principal
from squid.api.v1.schemas.submission_media import DraftMediaListResponse, DraftMediaResponse
from squid.api.v1.submission_media import (
    get_media_jobs,
    get_submission_drafts,
    router,
    upload_draft_media,
)
from squid.api.v1.submissions import authenticated_account
from squid.media.application.jobs import (
    MediaArtifactRole,
    MediaJobSnapshot,
    MediaJobStatus,
    MediaUploadMetadata,
    StagedMediaUploadSubmission,
    StoredMediaArtifact,
)
from squid.media.domain import MediaKind, MediaLimits
from squid.submissions.application import StoredDraft
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.errors import DraftAccessDeniedError

pytestmark = pytest.mark.asyncio

ACCOUNT_ID = 42
DRAFT_ID = UUID("84ab2da9-c27e-4d37-98c6-973bcc92f5e4")
UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")
OTHER_UPLOAD_ID = UUID("eca19583-1409-43a3-b1f9-fbc73076cc40")
NOW = Instant.parse_iso("2026-08-11T12:00:00Z")


def stored_draft(draft_id: UUID = DRAFT_ID) -> StoredDraft:
    return StoredDraft(
        snapshot=DraftSnapshot(
            id=draft_id,
            owner_account_id=ACCOUNT_ID,
            schema_id="build_submission.v1",
            schema_revision=1,
            category="door",
        ),
        origin=SubmissionOrigin.WEB,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )


def snapshot(
    *,
    upload_id: UUID = UPLOAD_ID,
    draft_id: UUID = DRAFT_ID,
    kind: MediaKind = MediaKind.VIDEO,
    status: MediaJobStatus = MediaJobStatus.PENDING,
    artifacts: tuple[StoredMediaArtifact, ...] = (),
) -> MediaJobSnapshot:
    return MediaJobSnapshot(
        upload=MediaUploadMetadata(
            id=upload_id,
            draft_id=draft_id,
            kind=kind,
            source_content_type=f"{kind.value}/mp4" if kind is MediaKind.VIDEO else "image/png",
            source_byte_size=4,
            source_sha256="a" * 64,
            source_object_key=f"private/raw/{upload_id}",
            strip_audio=kind is MediaKind.VIDEO,
            created_at=NOW,
        ),
        status=status,
        attempts=0,
        available_at=NOW,
        claimed_at=None,
        claim_token=None,
        completed_at=NOW if status is MediaJobStatus.COMPLETED else None,
        dead_at=NOW if status is MediaJobStatus.DEAD else None,
        discarded_at=NOW if status is MediaJobStatus.DISCARDED else None,
        last_error="private decoder detail" if status is MediaJobStatus.DEAD else None,
        artifacts=artifacts,
    )


class FakeDrafts:
    def __init__(self, events: list[str], *, deny: bool = False) -> None:
        self.events = events
        self.deny = deny

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        self.events.append("owner")
        assert draft_id == DRAFT_ID
        assert account_id == ACCOUNT_ID
        if self.deny:
            raise DraftAccessDeniedError
        return stored_draft(draft_id)


class FakeMedia:
    def __init__(self, events: list[str], *, failure: BaseException | None = None) -> None:
        self.events = events
        self.limits = MediaLimits(
            max_images=2,
            max_videos=1,
            max_duration_milliseconds=1_000,
            max_source_bytes=8,
            max_output_bytes=16,
            max_pixels_per_frame=100,
            max_decoded_pixels_per_second=200,
        )
        self.failure = failure
        self.snapshots: dict[UUID, MediaJobSnapshot] = {}
        self.staged_path: Path | None = None
        self.staged_parent: Path | None = None
        self.staged_mode: int | None = None
        self.parent_mode: int | None = None
        self.staged_bytes: bytes | None = None
        self.submission: StagedMediaUploadSubmission | None = None
        self.discarded: tuple[UUID, UUID] | None = None

    async def submit_staged(self, submission: StagedMediaUploadSubmission) -> UUID:
        self.events.append("submit")
        self.submission = submission
        self.staged_path = submission.source_path
        self.staged_parent = submission.source_path.parent
        self.staged_mode = stat.S_IMODE(submission.source_path.stat().st_mode)
        self.parent_mode = stat.S_IMODE(submission.source_path.parent.stat().st_mode)
        self.staged_bytes = submission.source_path.read_bytes()
        if self.failure is not None:
            raise self.failure
        upload_id = submission.upload_id or uuid4()
        self.snapshots[upload_id] = snapshot(
            upload_id=upload_id,
            draft_id=submission.draft_id,
            kind=submission.kind,
        )
        return upload_id

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        return self.snapshots.get(upload_id)

    async def list_for_draft(self, draft_id: UUID) -> tuple[MediaJobSnapshot, ...]:
        return tuple(item for item in self.snapshots.values() if item.upload.draft_id == draft_id)

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        self.discarded = (draft_id, upload_id)
        current = self.snapshots.get(upload_id)
        if current is None or current.upload.draft_id != draft_id:
            return False
        self.snapshots[upload_id] = replace(
            current,
            status=MediaJobStatus.DISCARDED,
            completed_at=None,
            dead_at=None,
            discarded_at=NOW,
            artifacts=(),
        )
        return True


def app_with_fakes(media: FakeMedia, drafts: FakeDrafts) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def media_dependency() -> FakeMedia:
        return media

    async def draft_dependency() -> FakeDrafts:
        return drafts

    async def account_dependency() -> int:
        return ACCOUNT_ID

    async def principal_dependency() -> Principal:
        return Principal(kind="account", subject=f"account:{ACCOUNT_ID}", account_id=ACCOUNT_ID)

    app.dependency_overrides[get_media_jobs] = media_dependency
    app.dependency_overrides[get_submission_drafts] = draft_dependency
    app.dependency_overrides[authenticated_account] = account_dependency
    app.dependency_overrides[current_principal] = principal_dependency
    return app


async def test_upload_streams_to_private_file_and_returns_only_safe_state() -> None:
    events: list[str] = []
    media = FakeMedia(events)
    app = app_with_fakes(media, FakeDrafts(events))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/submissions/drafts/{DRAFT_ID}/media/video",
            params={"strip_audio": "true", "upload_id": str(UPLOAD_ID)},
            headers={"Content-Type": "Video/MP4", "Content-Length": "4"},
            content=b"abcd",
        )

    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Squid-Max-Upload-Bytes"] == "8"
    assert response.json() == {
        "id": str(UPLOAD_ID),
        "draft_id": str(DRAFT_ID),
        "kind": "video",
        "status": "processing",
        "source_content_type": "video/mp4",
        "artifacts": [],
    }
    assert events == ["owner", "submit"]
    assert media.staged_bytes == b"abcd"
    assert media.staged_mode == 0o600
    assert media.parent_mode == 0o700
    assert media.submission is not None
    assert media.submission.strip_audio is True
    assert media.staged_path is not None
    assert not media.staged_path.exists()
    assert media.staged_parent is not None
    assert not media.staged_parent.exists()


async def test_list_get_and_discard_keep_stable_states_without_private_artifacts() -> None:
    events: list[str] = []
    media = FakeMedia(events)
    output = StoredMediaArtifact(
        role=MediaArtifactRole.OUTPUT,
        object_key="private/normalized/secret",
        content_type="video/mp4",
        byte_size=10,
        sha256="b" * 64,
        width=8,
        height=6,
    )
    poster = StoredMediaArtifact(
        role=MediaArtifactRole.POSTER,
        object_key="private/posters/secret",
        content_type="image/jpeg",
        byte_size=3,
        sha256="c" * 64,
        width=8,
        height=6,
    )
    report = StoredMediaArtifact(
        role=MediaArtifactRole.REPORT,
        object_key="private/reports/secret",
        content_type="application/json",
        byte_size=20,
        sha256="d" * 64,
        width=None,
        height=None,
    )
    media.snapshots[UPLOAD_ID] = snapshot(
        status=MediaJobStatus.COMPLETED,
        artifacts=(report, poster, output),
    )
    media.snapshots[OTHER_UPLOAD_ID] = snapshot(upload_id=OTHER_UPLOAD_ID, status=MediaJobStatus.DEAD)
    app = app_with_fakes(media, FakeDrafts(events))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_response = await client.get(f"/submissions/drafts/{DRAFT_ID}/media")
        get_response = await client.get(f"/submissions/drafts/{DRAFT_ID}/media/{UPLOAD_ID}")
        delete_response = await client.delete(f"/submissions/drafts/{DRAFT_ID}/media/{OTHER_UPLOAD_ID}")

    assert list_response.status_code == 200
    assert list_response.headers["Cache-Control"] == "no-store"
    assert list_response.json()["limits"] == {
        "max_upload_bytes": 8,
        "max_images": 2,
        "max_videos": 1,
        "max_output_bytes": 16,
        "max_duration_milliseconds": 1_000,
        "max_pixels_per_frame": 100,
        "max_decoded_pixels_per_second": 200,
    }
    listed = {item["id"]: item for item in list_response.json()["media"]}
    assert listed[str(UPLOAD_ID)]["status"] == "completed"
    assert listed[str(OTHER_UPLOAD_ID)]["status"] == "dead"
    assert listed[str(UPLOAD_ID)]["artifacts"] == [
        {"role": "output", "content_type": "video/mp4", "width": 8, "height": 6},
        {"role": "poster", "content_type": "image/jpeg", "width": 8, "height": 6},
    ]
    serialized = list_response.text
    assert "object_key" not in serialized
    assert "sha256" not in serialized
    assert "last_error" not in serialized
    assert "private decoder detail" not in serialized
    assert "report" not in serialized
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "completed"
    assert delete_response.status_code == 204
    assert delete_response.headers["Cache-Control"] == "no-store"
    assert media.discarded == (DRAFT_ID, OTHER_UPLOAD_ID)


async def test_upload_rejects_ambiguous_or_over_limit_framing_before_registration() -> None:
    cases = (
        ({"Content-Type": "image/png", "Content-Length": "0"}, {}, b"", "content_length_invalid"),
        ({"Content-Type": "image/png", "Content-Length": "9"}, {}, b"", "limit_exceeded"),
        ({"Content-Type": "image/png", "Content-Length": "5"}, {}, b"four", "content_length_mismatch"),
        (
            {"Content-Type": "image/png", "Content-Length": "4"},
            {"strip_audio": "true"},
            b"four",
            "strip_audio_requires_video",
        ),
        ({"Content-Type": "image/png", "Content-Length": "4"}, {"unknown": "1"}, b"four", "query_parameters_invalid"),
    )
    for headers, params, body, reason in cases:
        events: list[str] = []
        media = FakeMedia(events)
        app = app_with_fakes(media, FakeDrafts(events))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/submissions/drafts/{DRAFT_ID}/media/image",
                params=params,
                headers=headers,
                content=body,
            )
        assert response.status_code == 400
        assert response.json()["context"]["reason"] == reason
        assert media.submission is None
        assert events == ["owner"]


async def test_upload_checks_draft_ownership_before_inspecting_or_staging_body() -> None:
    events: list[str] = []
    media = FakeMedia(events)
    app = app_with_fakes(media, FakeDrafts(events, deny=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/submissions/drafts/{DRAFT_ID}/media/video",
            headers={"Content-Type": "not a media type", "Content-Length": "4"},
            content=b"four",
        )

    assert response.status_code == 403
    assert events == ["owner"]
    assert media.submission is None


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(RuntimeError("storage failed"), id="error"),
        pytest.param(asyncio.CancelledError(), id="cancellation"),
    ],
)
async def test_upload_cleans_private_stage_when_registration_aborts(failure: BaseException) -> None:
    events: list[str] = []
    media = FakeMedia(events, failure=failure)
    drafts = FakeDrafts(events)
    chunks = iter((b"ab", b"cd"))

    async def receive() -> dict[str, object]:
        try:
            chunk = next(chunks)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": chunk == b"ab"}

    from starlette.requests import Request
    from starlette.responses import Response

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": f"/submissions/drafts/{DRAFT_ID}/media/video",
            "raw_path": b"/submissions/drafts/media/video",
            "query_string": b"upload_id=75043a53-05ae-4097-bbf4-4eae1d6b088c",
            "headers": [(b"content-type", b"video/mp4"), (b"content-length", b"4")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        },
        receive,
    )

    with pytest.raises(type(failure)):
        await upload_draft_media(
            draft_id=DRAFT_ID,
            kind=MediaKind.VIDEO,
            request=request,
            response=Response(),
            media=media,
            drafts=drafts,
            account_id=ACCOUNT_ID,
            strip_audio=False,
            upload_id=UPLOAD_ID,
        )

    assert media.staged_path is not None
    assert not media.staged_path.exists()
    assert media.staged_parent is not None
    assert not media.staged_parent.exists()


async def test_cross_draft_lookup_and_missing_discard_are_not_visible() -> None:
    events: list[str] = []
    media = FakeMedia(events)
    media.snapshots[UPLOAD_ID] = snapshot(draft_id=uuid4())
    app = app_with_fakes(media, FakeDrafts(events))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_response = await client.get(f"/submissions/drafts/{DRAFT_ID}/media/{UPLOAD_ID}")
        delete_response = await client.delete(f"/submissions/drafts/{DRAFT_ID}/media/{OTHER_UPLOAD_ID}")

    assert get_response.status_code == 404
    assert delete_response.status_code == 404


async def test_media_dtos_reject_unknown_fields() -> None:
    completed = DraftMediaResponse.from_snapshot(snapshot())
    with pytest.raises(PydanticValidationError):
        DraftMediaResponse.model_validate({**completed.model_dump(), "source_object_key": "private/raw"})

    limits = {
        "max_upload_bytes": 8,
        "max_images": 2,
        "max_videos": 1,
        "max_output_bytes": 16,
        "max_duration_milliseconds": 1_000,
        "max_pixels_per_frame": 100,
        "max_decoded_pixels_per_second": 200,
    }
    with pytest.raises(PydanticValidationError):
        DraftMediaListResponse.model_validate({"limits": limits, "media": [], "report": {}})


async def test_openapi_advertises_streaming_binary_upload_without_a_json_wrapper() -> None:
    events: list[str] = []
    operation = app_with_fakes(FakeMedia(events), FakeDrafts(events)).openapi()["paths"][
        "/submissions/drafts/{draft_id}/media/{kind}"
    ]["post"]

    request_body = operation["requestBody"]
    assert request_body["required"] is True
    assert request_body["content"] == {
        "image/*": {"schema": {"type": "string", "format": "binary"}},
        "video/*": {"schema": {"type": "string", "format": "binary"}},
    }


async def test_disabled_media_service_fails_closed_with_service_unavailable() -> None:
    events: list[str] = []
    drafts = FakeDrafts(events)
    app = FastAPI()
    app.state.runtime = SimpleNamespace(services=SimpleNamespace(media_jobs=None, submission_drafts=drafts))
    register_exception_handlers(app)
    app.include_router(router)

    async def account_dependency() -> int:
        return ACCOUNT_ID

    app.dependency_overrides[authenticated_account] = account_dependency
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/submissions/drafts/{DRAFT_ID}/media")

    assert response.status_code == 503
    assert response.json()["resource"] == "submission_media"
    assert events == []
