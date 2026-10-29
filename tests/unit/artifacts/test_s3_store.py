"""S3 download ownership and byte budgets without a network service."""

import asyncio
import io
import threading
from unittest.mock import Mock

import anyio
import pytest

from squid.artifacts.infrastructure import ArtifactTooLargeError, S3ArtifactStore
from squid.config import ObjectStorageConfig


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Mock:
    client = Mock()
    session = Mock()
    session.client.return_value = client
    monkeypatch.setattr("boto3.session.Session", Mock(return_value=session))
    return client


@pytest.fixture
def store(client: Mock) -> S3ArtifactStore:
    return S3ArtifactStore(ObjectStorageConfig(backend="s3", bucket="test"))


@pytest.mark.parametrize("max_bytes", [0, -1, -2])
async def test_invalid_budget_never_opens_a_response(store: S3ArtifactStore, client: Mock, max_bytes: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        await store.get("object", max_bytes=max_bytes)

    client.get_object.assert_not_called()


@pytest.mark.parametrize("advertised_size", [4, 0])
async def test_oversized_response_is_closed(store: S3ArtifactStore, client: Mock, advertised_size: int) -> None:
    body = io.BytesIO(b"1234")
    client.get_object.return_value = {"ContentLength": advertised_size, "Body": body}

    with pytest.raises(ArtifactTooLargeError):
        await store.get("object", max_bytes=3)

    assert body.closed


async def test_successful_download_closes_response(store: S3ArtifactStore, client: Mock) -> None:
    body = io.BytesIO(b"payload")
    client.get_object.return_value = {"ContentLength": 7, "Body": body}

    assert await store.get("object", max_bytes=7) == b"payload"
    assert body.closed


async def test_cancellation_during_acquisition_still_closes_response(store: S3ArtifactStore, client: Mock) -> None:
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class ResponseBody(io.BytesIO):
        def close(self) -> None:
            super().close()
            closed.set()

    body = ResponseBody(b"payload")

    def get_object(**_kwargs: object) -> dict[str, object]:
        started.set()
        assert release.wait(timeout=5), "Test did not release the S3 response"
        return {"ContentLength": 7, "Body": body}

    client.get_object.side_effect = get_object

    async def download() -> None:
        await store.get("object", max_bytes=7)

    try:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(download)
                assert await asyncio.to_thread(started.wait, 5)
                tasks.cancel_scope.cancel()
    finally:
        release.set()

    assert await asyncio.to_thread(closed.wait, 5), "Cancelled download leaked its response"


async def test_invalid_response_metadata_still_closes_body(store: S3ArtifactStore, client: Mock) -> None:
    body = io.BytesIO(b"payload")
    client.get_object.return_value = {"ContentLength": "invalid", "Body": body}

    with pytest.raises(ValueError, match="invalid literal"):
        await store.get("object", max_bytes=7)

    assert body.closed
