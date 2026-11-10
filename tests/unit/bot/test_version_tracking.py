"""Version announcements received through Discord messages."""

from types import SimpleNamespace
from typing import cast, override
from unittest.mock import AsyncMock

import discord
import pytest

from squid.bot.version_tracking import VersionTracker
from squid.settings.domain.runtime import RuntimeSettings
from squid.versions.application import VersionService
from squid.versions.domain import Edition, MinecraftVersion, parse_version_string
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services
from tests.support.settings import RuntimeSettingsStub


class VersionRecorder(VersionService):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failure: Exception | None = None

    @override
    async def add(self, version_string: str, *, edition: Edition | None = None) -> MinecraftVersion:
        self.calls.append(version_string)
        if self.failure is not None:
            raise self.failure
        return MinecraftVersion(*parse_version_string(version_string))


@pytest.mark.parametrize(
    ("content", "author_id", "channel_id", "expected_calls", "expected_replies"),
    [
        ("Version added successfully: Java 1.21.4", 1, 10, [], 0),
        ("", 1, 10, [], 0),
        ("Java 1.21.4", 1, 10, [], 0),
        ("Java 1.21.4", 2, 11, [], 0),
        ("Thanks!", 2, 10, ["Thanks!"], 0),
        ("", 2, 10, [""], 0),
        ("Java 1.21.4\nRelease notes", 2, 10, ["Java 1.21.4"], 1),
        ("Bedrock 1.21.50", 3, 10, ["Bedrock 1.21.50"], 1),
    ],
)
async def test_version_announcements(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    author_id: int,
    channel_id: int,
    expected_calls: list[str],
    expected_replies: int,
) -> None:
    versions = VersionRecorder()
    bot = make_layout_bot(
        services=bot_services(
            versions=versions, settings=RuntimeSettingsStub(RuntimeSettings(version_tracker_channel_id=10))
        ),
        owner_server_id=123,
        user=SimpleNamespace(id=1, bot=True),
        community_config=SimpleNamespace(version_tracker_channel_id=10),
        get_channel=lambda channel_id: SimpleNamespace(id=channel_id),
    )
    message = cast(
        discord.Message,
        SimpleNamespace(
            guild=SimpleNamespace(id=123),
            content=content,
            author=SimpleNamespace(id=author_id, bot=author_id in (1, 3)),
            channel=SimpleNamespace(id=channel_id),
        ),
    )
    send = AsyncMock()
    monkeypatch.setattr("squid.bot.version_tracking.resolve_locale", AsyncMock(return_value="en"))
    monkeypatch.setattr("squid.bot.version_tracking.send_to", lambda channel: send)

    await VersionTracker(bot).on_message_version_add(message)

    assert versions.calls == expected_calls
    assert send.await_count == expected_replies


async def test_storage_errors_propagate() -> None:
    versions = VersionRecorder()
    versions.failure = RuntimeError("storage unavailable")
    bot = make_layout_bot(
        services=bot_services(
            versions=versions, settings=RuntimeSettingsStub(RuntimeSettings(version_tracker_channel_id=10))
        ),
        owner_server_id=123,
        user=SimpleNamespace(id=1),
        community_config=SimpleNamespace(version_tracker_channel_id=10),
    )
    message = cast(
        discord.Message,
        SimpleNamespace(
            guild=SimpleNamespace(id=123),
            content="Java 1.21.4",
            author=SimpleNamespace(id=2),
            channel=SimpleNamespace(id=10),
        ),
    )

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await VersionTracker(bot).on_message_version_add(message)
