"""Video requests pin inputs and never expose another account's result."""

import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, create_autospec

import pytest
from whenever import Instant

from squid.artifacts.infrastructure import LocalArtifactStore
from squid.core.errors import AuthorizationError, NotFoundError
from squid.schematics.application.attachments import StoredSchematic
from squid.schematics.application.jobs import SchematicJobService, SchematicJobSnapshot
from squid.schematics.application.ports import SchematicResourcePackProvider
from squid.schematics.application.video import VideoRenderRequest
from squid.schematics.application.video_service import SchematicVideoService
from squid.schematics.domain.values import VerifiedResourcePack
from tests.unit.schematics.fakes import FakeSchematicStore, make_analysis

type Components = tuple[SchematicVideoService, FakeSchematicStore, AsyncMock, LocalArtifactStore]


@pytest.fixture
def components(tmp_path: Path) -> tuple[SchematicVideoService, FakeSchematicStore, AsyncMock, LocalArtifactStore]:
    jobs = create_autospec(SchematicJobService, instance=True)
    jobs.submit.return_value = 41
    store = FakeSchematicStore()
    digest = hashlib.sha256(b"source").hexdigest()
    store.files[digest] = b"source"
    store.stored.append(
        StoredSchematic(
            id=1,
            build_id=7,
            file_sha256=digest,
            is_primary=True,
            original_filename="door.schem",
            analysis=make_analysis(sha256=digest),
            uploaded_by_account_id=10,
        )
    )
    pack = create_autospec(SchematicResourcePackProvider, instance=True)
    pack.load.return_value = VerifiedResourcePack.from_bytes(b"pack")
    artifacts = LocalArtifactStore(tmp_path)
    service = SchematicVideoService(jobs, artifacts, store, pack, enabled=True, max_blocks=1000, max_volume=1000)
    return service, store, jobs, artifacts


async def test_submission_pins_inputs_and_owner(components: Components) -> None:
    service, store, jobs, artifacts = components
    result = await service.submit_video(7, 1, VideoRenderRequest(), account_id=10, can_view_private=False)
    assert result.id == 41
    operation, params, keys = jobs.submit.call_args.args
    assert operation == "render_video"
    assert params["video"]["account_id"] == 10
    assert params["video"]["source_sha256"] == store.stored[0].file_sha256
    assert await artifacts.get(keys[0], max_bytes=100) == b"source"
    assert await artifacts.get(keys[1], max_bytes=100) == b"pack"


async def test_another_account_cannot_submit_a_private_source(components: Components) -> None:
    service, _, jobs, _ = components
    with pytest.raises(AuthorizationError):
        await service.submit_video(7, 1, VideoRenderRequest(), account_id=11, can_view_private=False)
    jobs.submit.assert_not_called()


async def test_download_rechecks_owner_source_and_expiry(components: Components) -> None:
    service, store, jobs, artifacts = components
    await service.submit_video(7, 1, VideoRenderRequest(), account_id=10, can_view_private=False)
    params = jobs.submit.call_args.args[1]
    snapshot = SchematicJobSnapshot(
        id=41,
        completed_at=Instant.now(),
        dead_at=None,
        result={},
        result_object_key="output.mp4",
        last_error=None,
        error_kind=None,
        error_context={},
        operation="render_video",
        params=params,
    )
    jobs.get.return_value = snapshot
    await artifacts.put("output.mp4", b"video", content_type="video/mp4")
    assert await service.open_video_content(41, account_id=10, can_view_private=False) == b"video"
    with pytest.raises(NotFoundError):
        await service.open_video_content(41, account_id=11, can_view_private=True)
    store.stored[0] = replace(store.stored[0], uploaded_by_account_id=12)
    with pytest.raises(AuthorizationError):
        await service.open_video_content(41, account_id=10, can_view_private=False)
    jobs.get.return_value = replace(snapshot, expires_at=Instant.now())
    assert (await service.get_video(41, account_id=10, can_view_private=True)).status == "expired"
    with pytest.raises(NotFoundError):
        await service.open_video_content(41, account_id=10, can_view_private=True)
