"""Tests for bounded local artifact storage semantics."""

import pytest

from squid.artifacts.infrastructure import ArtifactTooLargeError, LocalArtifactStore

pytestmark = pytest.mark.asyncio


async def test_local_artifacts_round_trip_and_report_metadata(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, prefix="test")

    metadata = await store.put("schematics/ab/file", b"payload", content_type="application/octet-stream")

    assert metadata.byte_size == 7
    assert metadata.sha256 == "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"
    assert await store.get("schematics/ab/file", max_bytes=7) == b"payload"
    stored = await store.stat("schematics/ab/file")
    assert stored is not None
    assert stored.byte_size == 7


async def test_local_artifact_downloads_are_bounded(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    await store.put("large", b"1234", content_type="application/octet-stream")

    with pytest.raises(ArtifactTooLargeError):
        await store.get("large", max_bytes=3)


async def test_local_artifact_keys_cannot_escape_the_storage_root(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="relative paths"):
        await store.put("../escape", b"payload", content_type="application/octet-stream")
