"""Transport and association acceptance tests for submission media routes."""

import asyncio
import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import Message, Receive

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
    receive: Receive | None = None,
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
    return Request(scope) if receive is None else Request(scope, receive)


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


async def test_nil_upload_identifiers_are_rejected_before_draft_authorization() -> None:
    nil = UUID(int=0)
    draft_events: list[str] = []
    upload_events: list[str] = []
    draft_app = app_with_fakes(FakeMedia(draft_events), FakeDrafts(draft_events))
    upload_app = app_with_fakes(FakeMedia(upload_events), FakeDrafts(upload_events))

    async with AsyncClient(transport=ASGITransport(app=draft_app), base_url="http://test") as client:
        nil_draft = await client.post(
            f"/submissions/drafts/{nil}/media/image",
            headers={"Content-Type": "image/png", "Content-Length": "1"},
            content=b"x",
        )
    async with AsyncClient(transport=ASGITransport(app=upload_app), base_url="http://test") as client:
        nil_upload = await client.post(
            f"/submissions/drafts/{DRAFT_ID}/media/image",
            params={"upload_id": str(nil)},
            headers={"Content-Type": "image/png", "Content-Length": "1"},
            content=b"x",
        )

    assert (nil_draft.status_code, nil_draft.json()["context"]["reason"]) == (400, "nil_draft_id")
    assert (nil_upload.status_code, nil_upload.json()["context"]["reason"]) == (400, "nil_upload_id")
    assert draft_events == []
    assert upload_events == []


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
        assert response.json()["context"]["reason"] == "content_length_mismatch"
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


async def test_upload_disconnect_closes_and_removes_partial_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "upload"
    directory.mkdir()
    monkeypatch.setattr(submission_media.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    messages: Iterator[Message] = iter(
        (
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.disconnect"},
        )
    )

    async def receive() -> Message:
        return next(messages)

    request = raw_request([(b"content-type", b"image/png"), (b"content-length", b"4")], receive=receive)

    with pytest.raises(ClientDisconnect):
        await upload_draft_media(
            draft_id=DRAFT_ID,
            kind=MediaKind.IMAGE,
            request=request,
            response=Response(),
            attachments=DraftAttachmentService(FakeDrafts([]), FakeMedia([])),
            account_id=ACCOUNT_ID,
            strip_audio=False,
            upload_id=UPLOAD_ID,
        )

    assert not directory.exists()


async def test_stream_open_and_permission_failures_do_not_leak_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = raw_request([])
    destination = tmp_path / "source"

    def deny_open(*_args: object, **_kwargs: object) -> int:
        raise PermissionError

    with monkeypatch.context() as patch:
        patch.setattr(submission_media.os, "open", deny_open)
        with pytest.raises(PermissionError):
            await submission_media._stream_to_private_file(request, destination, content_length=1, max_bytes=8)

    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    closed: list[int] = []
    real_close = os.close

    def close(recorded: int) -> None:
        closed.append(recorded)
        real_close(recorded)

    def deny_fchmod(*_args: object) -> None:
        raise PermissionError

    with monkeypatch.context() as patch:
        patch.setattr(submission_media.os, "open", lambda *_args, **_kwargs: descriptor)
        patch.setattr(submission_media.os, "fchmod", deny_fchmod)
        patch.setattr(submission_media.os, "close", close)
        with pytest.raises(PermissionError):
            await submission_media._stream_to_private_file(request, destination, content_length=1, max_bytes=8)

    assert closed == [descriptor]


async def test_zero_length_filesystem_write_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(submission_media.os, "write", lambda _descriptor, _data: 0)

    with pytest.raises(OSError, match="Unable to stage"):
        submission_media._write_all(1, b"data")


async def test_cancellation_waits_for_blocking_write_before_closing_and_removing_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "upload"
    directory.mkdir()
    monkeypatch.setattr(submission_media.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
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
    messages: Iterator[Message] = iter(({"type": "http.request", "body": b"data", "more_body": False},))

    async def receive() -> Message:
        return next(messages)

    request = raw_request([(b"content-type", b"image/png"), (b"content-length", b"4")], receive=receive)
    cancel_scope = anyio.CancelScope()
    settled = anyio.Event()

    async def upload() -> None:
        try:
            with cancel_scope:
                await upload_draft_media(
                    draft_id=DRAFT_ID,
                    kind=MediaKind.IMAGE,
                    request=request,
                    response=Response(),
                    attachments=DraftAttachmentService(FakeDrafts([]), FakeMedia([])),
                    account_id=ACCOUNT_ID,
                    strip_audio=False,
                    upload_id=UPLOAD_ID,
                )
        finally:
            settled.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(upload)
        with anyio.fail_after(2):
            while not started.is_set():
                await anyio.sleep(0)

        cancel_scope.cancel()
        await anyio.sleep(0)
        assert directory.exists()
        assert (directory / "source").exists()
        assert not settled.is_set()

        release.set()
        await settled.wait()

    assert not directory.exists()


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
