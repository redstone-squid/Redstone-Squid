"""Fetching messages, telling deleted ones from unreadable ones, and refreshing stored copies."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from whenever import Instant

from squid.bot.app import MessageLookup, RedstoneSquid


def _error(status: int) -> discord.HTTPException:
    response = MagicMock(status=status, reason="")
    return discord.NotFound(response, "gone") if status == 404 else discord.Forbidden(response, "hidden")


def _bot(*, cached: object | None = None, fetch_channel: Any = None) -> Any:
    return SimpleNamespace(get_channel=lambda channel_id: cached, fetch_channel=fetch_channel)


@pytest.mark.parametrize(("status", "gone"), [(404, True), (403, False)])
async def test_a_missing_or_hidden_message_is_reported_as_gone_or_unknown(status: int, gone: bool) -> None:
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(side_effect=_error(status))

    lookup = await RedstoneSquid.lookup_message(_bot(cached=channel), 1, 2)

    assert lookup == MessageLookup(None, gone=gone)


@pytest.mark.parametrize(("status", "gone"), [(404, True), (403, False)])
async def test_a_deleted_channel_means_its_messages_are_gone(status: int, gone: bool) -> None:
    lookup = await RedstoneSquid.lookup_message(_bot(fetch_channel=AsyncMock(side_effect=_error(status))), 1, 2)

    assert lookup == MessageLookup(None, gone=gone)


@dataclass
class Messages:
    refreshed: list[tuple[int, str | None, Instant | None]] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)

    async def refresh_content(self, message_id: int, content: str | None, edited_at: Instant | None) -> bool:
        self.refreshed.append((message_id, content, edited_at))
        return True

    async def mark_deleted(self, message_id: int) -> bool:
        self.deleted.append(message_id)
        return True


@pytest.mark.parametrize("outcome", ["found", "gone", "unreadable"])
async def test_fetching_a_stored_message_refreshes_or_tombstones_its_copy(outcome: str) -> None:
    edited = datetime(2026, 11, 8, 12, tzinfo=UTC)
    message = SimpleNamespace(id=2, content="edited offline", edited_at=edited)
    lookup = {
        "found": MessageLookup(cast(discord.Message, message)),
        "gone": MessageLookup(None, gone=True),
        "unreadable": MessageLookup(None),
    }[outcome]
    messages = Messages()

    async def lookup_message(channel_id: int, message_id: int) -> MessageLookup:
        return lookup

    bot = SimpleNamespace(lookup_message=lookup_message, services=SimpleNamespace(messages=messages))

    result = await RedstoneSquid.fetch_stored_message(cast(Any, bot), 1, 2)

    assert result is lookup.message
    assert messages.refreshed == ([(2, "edited offline", Instant(edited))] if outcome == "found" else [])
    assert messages.deleted == ([2] if outcome == "gone" else [])
