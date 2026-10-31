"""Tests for bounded local artifact storage semantics."""

from pathlib import Path

import pytest

from squid.artifacts.infrastructure import ArtifactTooLargeError, LocalArtifactStore

pytestmark = pytest.mark.asyncio


async def test_local_artifacts_round_trip_and_report_metadata(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, prefix="test")

    metadata = await store.put("schematics/ab/file", b"payload", content_type="application/octet-stream")

    assert metadata.byte_size == 7
    assert metadata.sha256 == "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"
    assert await store.get("schematics/ab/file", max_bytes=7) == b"payload"
    stored = await store.stat("schematics/ab/file")
    assert stored is not None
    assert stored.byte_size == 7


async def test_local_artifact_downloads_are_bounded(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    await store.put("large", b"1234", content_type="application/octet-stream")

    with pytest.raises(ArtifactTooLargeError):
        await store.get("large", max_bytes=3)


async def test_local_artifact_paths_stream_with_bounds_and_hashes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "objects")
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"streamed-payload")

    uploaded = await store.put_path(
        "media/raw",
        source,
        content_type="application/octet-stream",
        max_bytes=16,
    )
    downloaded = await store.get_path("media/raw", destination, max_bytes=16)

    assert uploaded == downloaded
    assert destination.read_bytes() == b"streamed-payload"

    with pytest.raises(ArtifactTooLargeError):
        await store.get_path("media/raw", destination, max_bytes=15)
    assert destination.read_bytes() == b"streamed-payload"


async def test_local_artifact_path_upload_rejects_symlinks(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "objects")
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    link = tmp_path / "link"
    # Windows refuses this without Developer Mode or SeCreateSymbolicLinkPrivilege, and a
    # test about *rejecting* symlinks cannot run where none can be made. Skipped rather
    # than xfailed: the guard under test is fine, the platform simply will not set it up.
    try:
        link.symlink_to(source)
    except OSError as error:
        pytest.skip(f"creating symlinks is not permitted here: {error}")

    with pytest.raises(ValueError, match="regular files"):
        await store.put_path(
            "media/raw",
            link,
            content_type="application/octet-stream",
            max_bytes=7,
        )


async def test_local_artifact_keys_cannot_escape_the_storage_root(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="relative paths"):
        await store.put("../escape", b"payload", content_type="application/octet-stream")
