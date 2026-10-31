"""Transport and association acceptance tests for submission media routes."""

import asyncio
import errno
import os
import shutil
import threading
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import Message

from squid.api.v1 import submission_media
from squid.api.v1.submission_media import upload_draft_media
from squid.media.domain import MediaKind
from squid.media.errors import DraftMediaRequestError
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


def raw_request(
    headers: list[tuple[bytes, bytes]],
    *,
    query_string: bytes = b"",
    messages: Sequence[Message] = (),
) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/media",
        "raw_path": b"/media",
        "query_string": query_string,
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
    }
    pending = iter(messages)

    async def receive() -> Message:
        return next(pending)

    return Request(scope, receive)


async def upload_image(request: Request, media: FakeMedia | None = None) -> None:
    media = media or FakeMedia([])
    await upload_draft_media(
        draft_id=DRAFT_ID,
        kind=MediaKind.IMAGE,
        request=request,
        response=Response(),
        attachments=DraftAttachmentService(FakeDrafts(media.events), media),
        account_id=ACCOUNT_ID,
        strip_audio=False,
        upload_id=UPLOAD_ID,
    )


@pytest.fixture
def staging_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(submission_media.tempfile, "tempdir", str(tmp_path))
    return tmp_path


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
    assert events == ["owner", "owner", "submit"]
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


@pytest.mark.parametrize(
    ("headers", "params", "body", "reason"),
    [
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
    ],
)
async def test_upload_rejects_ambiguous_or_over_limit_framing_before_registration(
    headers: dict[str, str], params: dict[str, str], body: bytes, reason: str
) -> None:
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


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        pytest.param([], "content_length_required_once", id="missing-length"),
        pytest.param(
            [(b"content-length", b"4"), (b"content-length", b"4")],
            "content_length_required_once",
            id="duplicate-length",
        ),
        pytest.param([(b"content-length", b"4, 4")], "content_length_invalid", id="combined-length"),
        pytest.param(
            [(b"transfer-encoding", b"chunked"), (b"content-length", b"4")],
            "transfer_encoding_not_supported",
            id="transfer-encoding",
        ),
        pytest.param(
            [(b"content-encoding", b"gzip"), (b"content-length", b"4")],
            "content_encoding_not_supported",
            id="content-encoding",
        ),
        pytest.param(
            [(b"content-encoding", b"\xff"), (b"content-length", b"4")],
            "content_encoding_not_supported",
            id="non-ascii-content-encoding",
        ),
    ],
)
async def test_raw_content_length_headers_reject_ambiguous_framing(
    headers: list[tuple[bytes, bytes]],
    reason: str,
) -> None:
    with pytest.raises(DraftMediaRequestError) as raised:
        submission_media._declared_content_length(raw_request(headers), max_bytes=8)

    assert raised.value.public_context == {"reason": reason}


async def test_unbounded_content_length_returns_structured_limit_error_without_integer_conversion() -> None:
    events: list[str] = []
    app = app_with_fakes(FakeMedia(events), FakeDrafts(events))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/submissions/drafts/{DRAFT_ID}/media/image",
            headers={"Content-Type": "image/png", "Content-Length": "9" * 4301},
            content=b"x",
        )

    assert response.status_code == 400
    assert response.json()["context"] == {
        "reason": "limit_exceeded",
        "violations": [{"measure": "source_bytes", "limit": 8}],
    }
    assert events == ["owner"]


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        pytest.param([], "content_type_required_once", id="missing"),
        pytest.param(
            [(b"content-type", b"image/png"), (b"content-type", b"image/png")],
            "content_type_required_once",
            id="duplicate",
        ),
        pytest.param([(b"content-type", b"video/mp4")], "content_type_kind_mismatch", id="wrong-kind"),
        pytest.param([(b"content-type", b"image/png; charset=utf-8")], "content_type_invalid", id="parameter"),
        pytest.param([(b"content-type", b"\xff")], "content_type_invalid", id="non-ascii"),
    ],
)
async def test_raw_content_type_headers_reject_ambiguous_media(
    headers: list[tuple[bytes, bytes]],
    reason: str,
) -> None:
    with pytest.raises(DraftMediaRequestError) as raised:
        submission_media._source_content_type(raw_request(headers), MediaKind.IMAGE)

    assert raised.value.public_context == {"reason": reason}


@pytest.mark.parametrize(
    ("draft_id", "params", "reason"),
    [
        pytest.param(UUID(int=0), {}, "nil_draft_id", id="draft"),
        pytest.param(DRAFT_ID, {"upload_id": str(UUID(int=0))}, "nil_upload_id", id="upload"),
    ],
)
async def test_nil_upload_identifiers_are_rejected_before_draft_authorization(
    draft_id: UUID, params: dict[str, str], reason: str
) -> None:
    events: list[str] = []
    app = app_with_fakes(FakeMedia(events), FakeDrafts(events))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/submissions/drafts/{draft_id}/media/image",
            params=params,
            headers={"Content-Type": "image/png", "Content-Length": "1"},
            content=b"x",
        )

    assert response.status_code == 400
    assert response.json()["context"]["reason"] == reason
    assert events == []


async def test_duplicate_query_parameter_is_rejected_after_authorization() -> None:
    events: list[str] = []
    app = app_with_fakes(FakeMedia(events), FakeDrafts(events))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/submissions/drafts/{DRAFT_ID}/media/image",
            params=[("strip_audio", "false"), ("strip_audio", "false")],
            headers={"Content-Type": "image/png", "Content-Length": "1"},
            content=b"x",
        )

    assert response.status_code == 400
    assert response.json()["context"]["reason"] == "query_parameters_invalid"
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
    request = raw_request(
        [(b"content-type", b"image/png"), (b"content-length", b"4")],
        messages=(
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"cd", "more_body": False},
        ),
    )

    with pytest.raises(type(failure)):
        await upload_image(request, media)

    assert media.staged_path is not None
    assert not media.staged_path.exists()
    assert media.staged_parent is not None
    assert not media.staged_parent.exists()


@pytest.mark.parametrize("body", [b"four", b"short"], ids=["success", "stream-error"])
async def test_upload_logs_cleanup_failure_without_changing_response(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    body: bytes,
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
                content=body,
            )

        if body == b"four":
            assert response.status_code == 202
        else:
            assert response.status_code == 400
            assert response.json()["context"]["reason"] == "content_length_mismatch"
        assert "Unable to remove a private upload staging directory" in caplog.text
        assert len(retained) == 1
    finally:
        for directory in retained:
            real_rmtree(directory, ignore_errors=True)


async def test_upload_disconnect_closes_and_removes_partial_stage(staging_root: Path) -> None:
    request = raw_request(
        [(b"content-type", b"image/png"), (b"content-length", b"4")],
        messages=(
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.disconnect"},
        ),
    )

    with pytest.raises(ClientDisconnect):
        await upload_image(request)

    assert list(staging_root.iterdir()) == []


async def test_stream_never_overwrites_an_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "source"
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        await submission_media._stream_to_private_file(raw_request([]), destination, content_length=1, max_bytes=8)

    assert destination.read_bytes() == b"existing"


@pytest.mark.skipif(not hasattr(os, "fchmod"), reason="requires POSIX descriptor permissions")
async def test_permission_failure_closes_the_open_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[int] = []

    def deny_fchmod(descriptor: int, mode: int) -> None:
        opened.append(descriptor)
        raise PermissionError("permission change refused")

    monkeypatch.setattr(submission_media.os, "fchmod", deny_fchmod)
    with pytest.raises(PermissionError, match="permission change refused"):
        await submission_media._stream_to_private_file(
            raw_request([]), tmp_path / "source", content_length=1, max_bytes=8
        )

    (descriptor,) = opened
    with pytest.raises(OSError, match=os.strerror(errno.EBADF)) as raised:
        os.fstat(descriptor)
    assert raised.value.errno == errno.EBADF


async def test_zero_length_filesystem_write_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(submission_media.os, "write", lambda _descriptor, _data: 0)

    with pytest.raises(OSError, match="Unable to stage"):
        submission_media._write_all(1, b"data")


async def test_cancellation_waits_for_blocking_write_before_closing_and_removing_stage(
    staging_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    real_write_all = submission_media._write_all

    def blocking_write(descriptor: int, data: bytes) -> None:
        started.set()
        if not release.wait(timeout=5):
            msg = "test did not release the blocking write"
            raise TimeoutError(msg)
        real_write_all(descriptor, data)

    monkeypatch.setattr(submission_media, "_write_all", blocking_write)
    request = raw_request(
        [(b"content-type", b"image/png"), (b"content-length", b"4")],
        messages=({"type": "http.request", "body": b"data", "more_body": False},),
    )
    cancel_scope = anyio.CancelScope()
    settled = anyio.Event()

    async def upload() -> None:
        try:
            with cancel_scope:
                await upload_image(request)
        finally:
            settled.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(upload)
        with anyio.fail_after(2):
            while not started.is_set():
                await anyio.sleep(0)

        try:
            cancel_scope.cancel()
            await anyio.sleep(0)
            (directory,) = staging_root.iterdir()
            assert (directory / "source").is_file()
            assert not settled.is_set()
        finally:
            release.set()
        with anyio.fail_after(2):
            await settled.wait()

    assert list(staging_root.iterdir()) == []


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
