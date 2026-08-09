"""Message bundle assembly primitives."""

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import discord
import pytest

from squid.bot.submission import message_context
from squid.bot.submission.message_context import collect_images, group_messages


@dataclass(frozen=True)
class FakeGroupMessage:
    id: int
    author_id: int
    created_at: datetime
    reference_id: int | None = None


def message(id: int, author: int, seconds: int, reference_id: int | None = None) -> FakeGroupMessage:
    return FakeGroupMessage(id, author, datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds), reference_id)


def test_group_messages_keeps_interleaved_authors_as_context() -> None:
    groups = group_messages([message(1, 10, 0), message(2, 20, 1), message(3, 10, 2)])

    assert len(groups) == 1
    assert [item.id for item in groups[0].primary] == [1, 3]
    assert [item.id for item in groups[0].context] == [2]


def test_group_messages_breaks_on_gap_cap_and_external_reply() -> None:
    groups = group_messages(
        [
            message(1, 10, 0),
            message(2, 10, 1),
            message(3, 10, 2),
            message(4, 10, 100),
            message(5, 10, 101, reference_id=999),
        ],
        window_seconds=10,
        max_messages=2,
    )

    assert [[item.id for item in group.primary] for group in groups] == [[1, 2], [3], [4], [5]]


class FakeAttachment:
    filename = "door.png"
    content_type = "image/png"
    size = 3
    url = "https://example.invalid/door.png"

    async def read(self) -> bytes:
        return b"png"


class FakeImageMessage:
    def __init__(self, id: int) -> None:
        self.id = id
        self.attachments = [FakeAttachment()]


async def test_collect_images_respects_count_and_byte_caps() -> None:
    messages = cast(list[discord.Message], [FakeImageMessage(1), FakeImageMessage(2)])

    count_limited = await collect_images(messages, max_images=1)
    byte_limited = await collect_images(messages, max_bytes=3)

    assert len(count_limited) == 1
    assert count_limited[0].source_message_id == 1
    assert len(byte_limited) == 1


async def test_video_preview_uses_downloaded_bytes_not_a_remote_ffmpeg_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment = FakeAttachment()
    attachment.filename = "door.mp4"
    attachment.content_type = "video/mp4"
    attachment.url = "https://127.0.0.1/private-video"
    attachment.read = AsyncMock(return_value=b"downloaded-video")  # type: ignore[method-assign]
    message = FakeImageMessage(1)
    message.attachments = [attachment]
    extract = AsyncMock(return_value=io.BytesIO(b"png-frame"))
    monkeypatch.setattr(message_context, "extract_first_frame", extract)

    images = await collect_images(cast(list[discord.Message], [message]))

    extract.assert_awaited_once_with(b"downloaded-video")
    assert images[0].data == b"png-frame"
