"""Completed videos are attached privately without repeating an acknowledged upload."""

from typing import Literal
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.schematic_tools import SchematicTools, _video_failure_reason
from squid.core.actors import Actor
from squid.schematics.application.services import SchematicService
from squid.schematics.application.video_service import SchematicVideoService, VideoStatus
from squid_ui.text import source_text
from squid_ui_discord.testing import InteractionHarness


@pytest.fixture
def delivery(monkeypatch: pytest.MonkeyPatch) -> tuple[SchematicTools, Mock, Mock]:
    service = Mock(spec=SchematicVideoService)
    service.get_video = AsyncMock(return_value=VideoStatus(id=41, build_id=7, schematic_id=1, status="succeeded"))
    service.open_video_content = AsyncMock(return_value=b"mp4")
    service.public_base_url = "https://squid.example"
    schematics = Mock(spec=SchematicService)
    schematics.videos = service
    component = SchematicTools(
        7, schematics=schematics, measure_timing=False, actor_account_id=10, authorize=AsyncMock(return_value=True)
    )
    request = Mock(spec=sd.Request)
    request.interaction = Mock(filesize_limit=100)
    request.interaction.is_expired.return_value = False
    request.respond = AsyncMock()
    monkeypatch.setattr(
        "squid.bot.submission.schematic_tools.actor_for_interaction", AsyncMock(return_value=Actor(account_id=10))
    )
    monkeypatch.setattr("squid.bot.submission.schematic_tools.allows", AsyncMock(return_value=True))
    return component, request, service


async def test_completed_video_is_attached_once_and_privately(delivery: tuple[SchematicTools, Mock, Mock]) -> None:
    component, request, service = delivery
    assert await component._deliver_video(request, 41)
    assert await component._deliver_video(request, 41)
    request.respond.assert_awaited_once()
    assert request.respond.call_args.kwargs["audience"] == "personal"
    service.open_video_content.assert_awaited_once_with(41, account_id=10, can_view_private=True)


async def test_oversized_video_uses_private_link(delivery: tuple[SchematicTools, Mock, Mock]) -> None:
    component, request, _ = delivery
    request.interaction.filesize_limit = 1
    assert await component._deliver_video(request, 41)
    assert "files" not in request.respond.call_args.kwargs
    assert request.respond.call_args.kwargs["audience"] == "personal"


async def test_expired_interaction_waits_for_a_fresh_refresh(delivery: tuple[SchematicTools, Mock, Mock]) -> None:
    component, request, _ = delivery
    request.interaction.is_expired.return_value = True
    assert await component._deliver_video(request, 41)
    request.respond.assert_not_awaited()
    request.interaction.is_expired.return_value = False
    assert await component._deliver_video(request, 41)
    request.respond.assert_awaited_once()


async def test_failed_upload_can_retry_from_the_stored_result(delivery: tuple[SchematicTools, Mock, Mock]) -> None:
    component, request, service = delivery
    request.respond.side_effect = OSError("transport failed")
    with pytest.raises(OSError, match="transport failed"):
        await component._deliver_video(request, 41)
    request.respond.side_effect = None
    assert await component._deliver_video(request, 41)
    assert service.open_video_content.await_count == 2


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("unavailable", "FFmpeg"),
        ("timeout", "time limit"),
        ("invalid", "worker logs"),
        ("ambiguous_simulation_input", "lever or button"),
        ("internal", "worker logs"),
    ],
)
def test_failure_message_explains_the_next_step(error: str, expected: str) -> None:
    assert expected in source_text(_video_failure_reason(error, expired=False))
    assert "expired" in source_text(_video_failure_reason(error, expired=True))


async def test_failed_video_reports_privately_without_fetching_content(
    delivery: tuple[SchematicTools, Mock, Mock],
) -> None:
    component, request, service = delivery
    service.get_video.return_value = VideoStatus(
        id=41, build_id=7, schematic_id=1, status="failed", error="unavailable"
    )
    assert await component._deliver_video(request, 41)
    request.respond.assert_awaited_once()
    assert request.respond.call_args.kwargs["audience"] == "personal"
    service.open_video_content.assert_not_awaited()


@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_check_video_shows_result_after_automatic_delivery(
    delivery: tuple[SchematicTools, Mock, Mock], status: Literal["succeeded", "failed"]
) -> None:
    component, request, service = delivery
    service.get_video.return_value = VideoStatus(id=41, build_id=7, schematic_id=1, status=status)
    component._video_job_id = 41
    assert await component._deliver_video(request, 41)
    request.respond.reset_mock()
    service.get_video.reset_mock()
    event = Mock(spec=sl.PressEvent, context=request, notice=AsyncMock())

    await component._refresh_video(event)

    service.get_video.assert_awaited_once_with(41, account_id=10, can_view_private=True)
    request.respond.assert_awaited_once()
    assert request.respond.call_args.kwargs["audience"] == "personal"
    event.notice.assert_not_awaited()
    request.respond.reset_mock()
    assert await component._deliver_video(request, 41)
    request.respond.assert_not_awaited()


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_check_video_reports_pending_render(
    delivery: tuple[SchematicTools, Mock, Mock], status: Literal["queued", "running"]
) -> None:
    component, request, service = delivery
    service.get_video.return_value = VideoStatus(id=41, build_id=7, schematic_id=1, status=status)
    component._video_job_id = 41
    event = Mock(spec=sl.PressEvent, context=request, notice=AsyncMock())

    await component._refresh_video(event)

    event.notice.assert_awaited_once()
    assert "still rendering" in source_text(event.notice.call_args.args[0])
    request.respond.assert_not_awaited()
    service.open_video_content.assert_not_awaited()


async def test_automatic_delivery_sends_a_visible_video_followup(
    delivery: tuple[SchematicTools, Mock, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    component, _, service = delivery
    service.get_video.side_effect = [
        VideoStatus(id=41, build_id=7, schematic_id=1, status="running"),
        VideoStatus(id=41, build_id=7, schematic_id=1, status="succeeded"),
    ]
    client = Mock(spec=discord.Client)
    runtime = sd.install(client)
    scope = runtime.scope(object())
    interaction = InteractionHarness(client=client)
    interaction.source.filesize_limit = 100
    request = await scope.request(interaction.source)
    await interaction.response.send_message("Video queued", ephemeral=True)
    monkeypatch.setattr("squid.bot.submission.schematic_tools.anyio.sleep", AsyncMock())

    try:
        await component._follow_video(request, 41)

        assert service.get_video.await_count == 2
        assert len(interaction.followup.send.records) == 1
        sent = interaction.followup.send.records[0].kwargs
        assert sent["ephemeral"] is True
        assert sent["files"][0].filename == "build-7-simulation.mp4"
        galleries = [item for item in sent["view"].walk_children() if isinstance(item, discord.ui.MediaGallery)]
        assert len(galleries) == 1
        assert galleries[0].items[0].media.url == "attachment://build-7-simulation.mp4"
        assert 41 in component._delivered_videos
    finally:
        await scope.close()


@pytest.mark.parametrize(
    ("media_type", "filename", "preview"),
    [("image/png", "render.png", True), ("application/octet-stream", "build.litematic", False)],
)
async def test_schematic_results_preview_images_and_download_schematics(
    delivery: tuple[SchematicTools, Mock, Mock],
    monkeypatch: pytest.MonkeyPatch,
    media_type: str,
    filename: str,
    preview: bool,
) -> None:
    component, _, _ = delivery
    monkeypatch.setattr(component._schematics, "available", False)
    component._asset = sl.document.Asset("result", filename, media_type, sl.document.InlineAsset(b"result"))
    client = Mock(spec=discord.Client)
    scope = sd.install(client).scope(object())
    try:
        payload = scope.render(component)
        assert payload.view is not None
        galleries = [item for item in payload.view.walk_children() if isinstance(item, discord.ui.MediaGallery)]
        downloads = [item for item in payload.view.walk_children() if isinstance(item, discord.ui.File)]
        assert len(galleries) == int(preview)
        assert len(downloads) == int(not preview)
        if preview:
            assert galleries[0].items[0].media.url == f"attachment://{filename}"
        files = payload.build_files()
        try:
            assert [file.filename for file in files] == [filename]
        finally:
            for file in files:
                file.close()
    finally:
        await scope.close()
