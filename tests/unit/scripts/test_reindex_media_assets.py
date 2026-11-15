"""Asset repair recovers only the original bytes and requires an audit for writes."""

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scripts.reindex_media_assets import parse_args, recover_source


@pytest.mark.parametrize("arguments", [["--execute"], ["--limit", "0"], ["--concurrency", "0"]])
def test_invalid_repair_options_are_rejected(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(arguments)


async def test_repair_reuses_verified_storage_without_discord(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"original")
    store = AsyncMock()
    client = AsyncMock()
    row: dict[str, Any] = {
        "source_byte_size": 8,
        "source_sha256": hashlib.sha256(b"original").hexdigest(),
        "source_object_key": "raw/source",
    }

    await recover_source(row, source, store, client)

    store.get_path.assert_awaited_once_with("raw/source", source, max_bytes=8)
    client.fetch_channel.assert_not_called()


async def test_repair_rejects_corrupted_original(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"modified")
    row: dict[str, Any] = {
        "source_byte_size": 8,
        "source_sha256": hashlib.sha256(b"original").hexdigest(),
        "source_object_key": "raw/source",
    }
    with pytest.raises(ValueError, match="checksum mismatch"):
        await recover_source(row, source, AsyncMock(), AsyncMock())
