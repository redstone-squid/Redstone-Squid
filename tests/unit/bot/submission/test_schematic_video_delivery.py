"""Completed videos are attached privately without repeating an acknowledged upload."""

from unittest.mock import AsyncMock, Mock

import pytest

import squid_ui_discord as sd
from squid.bot.submission.schematic_tools import SchematicTools
from squid.core.actors import Actor
from squid.schematics.application.services import SchematicService
from squid.schematics.application.video_service import SchematicVideoService, VideoStatus


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
    assert request.respond.call_args.kwargs["files"][0].filename == "build-7-simulation.mp4"
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
