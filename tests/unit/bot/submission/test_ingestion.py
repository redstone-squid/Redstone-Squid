"""Discord bundle ingestion boundary tests."""

from typing import cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from squid.bot.submission import ingestion
from squid.builds.domain import Build, BuildCategory, BuildDraft
from squid.runtime import BotServices
from squid.schematics.application import IngestedSchematic


async def test_raw_schematic_is_recorded_privately_without_public_mirroring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment = Mock(
        filename="door.litematic",
        content_type="application/octet-stream",
        size=128,
        read=AsyncMock(return_value=b"schematic-bytes"),
    )
    message = Mock(
        id=100,
        attachments=[attachment],
        author=Mock(id=200),
        guild=Mock(id=300),
        channel=Mock(id=400),
        content="submission",
    )
    draft = BuildDraft(category=BuildCategory.DOOR)
    services = Mock()
    services.build_inference.infer = AsyncMock(return_value=[draft])
    services.schematics.limits.max_upload_bytes = 1024
    services.schematics.available = True
    services.schematics.ingest = AsyncMock(return_value=cast(IngestedSchematic, object()))
    services.schematics.find_duplicates = AsyncMock(return_value=())
    services.schematics.record = AsyncMock()

    async def submit(saved: Build, **_kwargs: object) -> None:
        saved.id = 42

    services.builds.submit = AsyncMock(side_effect=submit)
    services.messages.track = AsyncMock()
    mirror = Mock(upload=AsyncMock())
    monkeypatch.setattr(ingestion, "assemble_bundle", AsyncMock(return_value=object()))

    result = await ingestion.ingest_message_bundle(
        [cast(discord.Message, message)],
        [],
        cast(BotServices, services),
        model="test-model",
        mirror=mirror,
    )

    (build,) = result
    assert build.id == 42
    assert build.schematic_urls == ()
    mirror.upload.assert_not_awaited()
    services.schematics.record.assert_awaited_once()
