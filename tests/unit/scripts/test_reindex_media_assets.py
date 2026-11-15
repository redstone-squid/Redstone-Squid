"""Asset repair recovers only the original bytes and requires an audit for writes."""

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
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


@pytest.mark.parametrize("declared_size", [4, 8, 16])
async def test_repair_recovers_attachment_from_followup_message(tmp_path: Path, declared_size: int) -> None:
    destination = tmp_path / "source"
    data = b"original"
    attachment = MagicMock(id=42, size=declared_size)

    async def save(path: Path) -> None:
        path.write_bytes(data)

    attachment.save = AsyncMock(side_effect=save)
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(side_effect=[MagicMock(attachments=[]), MagicMock(attachments=[attachment])])
    client = AsyncMock()
    client.fetch_channel.return_value = channel
    store = AsyncMock()
    store.get_path.return_value = None
    row: dict[str, Any] = {
        "id": "source-id",
        "source_files": [{"id": "source-id", "url": "https://cdn.discordapp.com/attachments/10/42/image.png"}],
        "source_byte_size": len(data),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_object_key": "raw/source",
        "source_messages": [{"channel_id": 10, "id": 100}],
        "bundle_channel_id": 10,
        "bundle_message_ids": [100, 101],
    }

    await recover_source(row, destination, store, client)

    assert destination.read_bytes() == data
    assert [call.args for call in channel.fetch_message.await_args_list] == [(100,), (101,)]
    client.fetch_channel.assert_awaited_once_with(10)
