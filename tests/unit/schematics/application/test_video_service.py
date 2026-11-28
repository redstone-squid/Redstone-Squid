"""Video requests pin inputs and never expose another account's result."""

import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, create_autospec

import pytest
from whenever import Instant

from squid.core.actors import ActingContext, UserActor
from squid.core.artifacts.infrastructure import LocalArtifactStore
from squid.core.errors import AuthorizationError, NotFoundError
from squid.core.pagination import Page
from squid.core.permissions.application import PermissionService
from squid.core.schematics.application.attachments import StoredSchematic
from squid.core.schematics.application.jobs import (
    SchematicJobService,
    SchematicJobSnapshot,
    VideoListFilters,
)
from squid.core.schematics.application.ports import SchematicResourcePackProvider
from squid.core.schematics.application.video import VideoRenderRequest
from squid.core.schematics.application.video_service import SchematicVideoService
from squid.core.schematics.domain.values import VerifiedResourcePack
from tests.support.permissions import permission_uow_factory
from tests.unit.api.fakes import EmptyPermissionStore
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
    service = SchematicVideoService(
        jobs,
        artifacts,
        store,
        pack,
        permissions=PermissionService(uow_factory=permission_uow_factory(EmptyPermissionStore())),
        enabled=True,
        max_blocks=1000,
        max_volume=1000,
    )
    return service, store, jobs, artifacts


async def test_submission_pins_inputs_and_owner(components: Components) -> None:
    service, store, jobs, artifacts = components
    result = await service.submit_video(
        7, 1, VideoRenderRequest(), context=ActingContext.direct(UserActor(account_id=10))
    )
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
        await service.submit_video(
            7,
            1,
            VideoRenderRequest(),
            context=ActingContext.direct(UserActor(account_id=11)),
        )
    jobs.submit.assert_not_called()


async def test_download_rechecks_owner_source_and_expiry(components: Components) -> None:
    service, store, jobs, artifacts = components
    await service.submit_video(7, 1, VideoRenderRequest(), context=ActingContext.direct(UserActor(account_id=10)))
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
    assert await service.open_video_content(41, context=ActingContext.direct(UserActor(account_id=10))) == b"video"
    with pytest.raises(NotFoundError):
        await service.open_video_content(41, context=ActingContext.direct(UserActor(account_id=11, is_bot_owner=True)))
    store.stored[0] = replace(store.stored[0], uploaded_by_account_id=12)
    with pytest.raises(AuthorizationError):
        await service.open_video_content(41, context=ActingContext.direct(UserActor(account_id=10)))
    jobs.get.return_value = replace(snapshot, expires_at=Instant.now())
    assert (
        await service.get_video(41, context=ActingContext.direct(UserActor(account_id=10, is_bot_owner=True)))
    ).status == "expired"
    with pytest.raises(NotFoundError):
        await service.open_video_content(41, context=ActingContext.direct(UserActor(account_id=10, is_bot_owner=True)))


async def test_listing_pages_the_callers_videos_with_their_status(components: Components) -> None:
    service, _, jobs, _ = components
    video = {"account_id": 10, "build_id": 7, "schematic_id": 1, "source_sha256": "a" * 64}
    finished = SchematicJobSnapshot(
        id=41,
        completed_at=Instant.now(),
        dead_at=None,
        result={},
        result_object_key="output.mp4",
        last_error=None,
        error_kind=None,
        error_context={},
        operation="render_video",
        params={"video": video},
    )
    jobs.list_videos.return_value = Page(
        items=(replace(finished, id=42, completed_at=None), finished), total=2, next=None, prev=None
    )

    page = await service.list_videos(
        filters=VideoListFilters(1), context=ActingContext.direct(UserActor(account_id=10))
    )

    assert [(item.id, item.status) for item in page.items] == [(42, "queued"), (41, "succeeded")]
    assert jobs.list_videos.call_args.args == (10,)
    assert jobs.list_videos.call_args.kwargs["filters"].schematic_id == 1
