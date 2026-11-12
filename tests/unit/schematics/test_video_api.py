"""Video HTTP responses preserve private caching and mounted download links."""

from unittest.mock import create_autospec

import httpx
from fastapi import FastAPI

from squid.api.dependencies import get_build_queries, get_permissions, get_schematics
from squid.api.idempotency import reserve_idempotent_request
from squid.api.security import Caller, current_caller
from squid.api.v1.schematics import router
from squid.permissions.application import PermissionService
from squid.schematics.application import SchematicService
from squid.schematics.application.video_service import SchematicVideoService, VideoStatus
from tests.unit.schematics.test_public_api import ConfirmedBuilds


async def test_video_http_flow_keeps_results_private_and_uses_mounted_links() -> None:
    videos = create_autospec(SchematicVideoService, instance=True)
    queued = VideoStatus(id=9, build_id=7, schematic_id=3, status="queued")
    videos.submit_video.return_value = queued
    videos.get_video.return_value = queued.model_copy(update={"status": "succeeded"})
    videos.open_video_content.return_value = b"private-mp4"
    schematics = create_autospec(SchematicService, instance=True)
    schematics.videos = videos
    caller = create_autospec(Caller, instance=True)
    caller.require_consented_account.return_value = 42
    caller.allows.return_value = False
    permissions = create_autospec(PermissionService, instance=True)
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_schematics] = lambda: schematics
    app.dependency_overrides[get_build_queries] = ConfirmedBuilds
    app.dependency_overrides[get_permissions] = lambda: permissions
    app.dependency_overrides[current_caller] = lambda: caller
    app.dependency_overrides[reserve_idempotent_request] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path="/catalogue"), base_url="https://test"
    ) as client:
        submitted = await client.post("/catalogue/v1/builds/7/schematics/3/videos", json={})
        assert submitted.status_code == 202
        assert submitted.headers["cache-control"] == "private, no-store"
        assert submitted.json()["download_url"] is None
        assert submitted.json()["status_url"] == "/catalogue/v1/schematic-videos/9"
        status = await client.get(submitted.json()["status_url"])
        assert status.status_code == 200
        assert status.headers["cache-control"] == "private, no-store"
        assert status.json()["download_url"] == "/catalogue/v1/schematic-videos/9/content"
        download = await client.get(status.json()["download_url"])
        assert download.status_code == 200
        assert download.content == b"private-mp4"
        assert download.headers["content-type"] == "video/mp4"
        assert download.headers["cache-control"] == "private, no-store"
        invalid = await client.post("/catalogue/v1/builds/7/schematics/3/videos", json={"width": 321})
        assert invalid.status_code == 422
    assert videos.submit_video.await_count == 1
    videos.open_video_content.assert_awaited_once_with(9, account_id=42, can_view_private=False)
