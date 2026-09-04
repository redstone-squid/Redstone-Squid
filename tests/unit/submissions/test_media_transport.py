"""Transport and association acceptance tests for submission media routes."""

import asyncio
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import Response

from squid.api.v1 import submission_media
from squid.api.v1.submission_media import upload_draft_media
from squid.media.domain import MediaKind
from squid.submissions.application import DraftAttachmentService
from tests.unit.submissions.media_api_fakes import (
    ACCOUNT_ID,
    DRAFT_ID,
    OTHER_UPLOAD_ID,
    UPLOAD_ID,
    FakeDrafts,
    FakeMedia,
    app_with_fakes,
    snapshot,
)

pytestmark = pytest.mark.asyncio


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
    if os.name == "posix":
        assert media.staged_mode == 0o600
        assert media.parent_mode == 0o700
    assert media.submission is not None
    assert media.submission.strip_audio is True
    assert media.staged_path is not None
    assert not media.staged_path.exists()
    assert media.staged_parent is not None
    assert not media.staged_parent.exists()


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
            attachments=DraftAttachmentService(drafts, media),
            account_id=ACCOUNT_ID,
            strip_audio=False,
            upload_id=UPLOAD_ID,
        )

    assert media.staged_path is not None
    assert not media.staged_path.exists()
    assert media.staged_parent is not None
    assert not media.staged_parent.exists()


async def test_upload_logs_tree_cleanup_failure_without_masking_stream_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real_rmtree = shutil.rmtree
    retained: list[Path] = []

    def fail_cleanup(path: str | os.PathLike[str], *_args: object, **_kwargs: object) -> None:
        retained.append(Path(path))
        raise PermissionError("cleanup refused")

    monkeypatch.setattr(submission_media.shutil, "rmtree", fail_cleanup)
    events: list[str] = []
    app = app_with_fakes(FakeMedia(events), FakeDrafts(events))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/submissions/drafts/{DRAFT_ID}/media/image",
                headers={"Content-Type": "image/png", "Content-Length": "4"},
                content=b"short",
            )

        assert response.status_code == 400
        assert "Unable to remove a private upload staging directory" in caplog.text
        assert len(retained) == 1
    finally:
        for directory in retained:
            real_rmtree(directory, ignore_errors=True)


async def test_private_upload_tree_logs_cleanup_failure_after_normal_exit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real_rmtree = shutil.rmtree
    retained: list[Path] = []

    def fail_cleanup(path: str | os.PathLike[str], *_args: object, **_kwargs: object) -> None:
        retained.append(Path(path))
        raise PermissionError("cleanup refused")

    monkeypatch.setattr(submission_media.shutil, "rmtree", fail_cleanup)
    try:
        with submission_media._private_upload_directory() as directory:
            (directory / "source").write_bytes(b"registered")

        assert "Unable to remove a private upload staging directory" in caplog.text
        assert retained == [directory]
    finally:
        for path in retained:
            real_rmtree(path, ignore_errors=True)


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
