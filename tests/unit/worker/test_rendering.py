"""Durable schematic preview publication with real storage and topic delivery."""

import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import override

import pytest

from squid.artifacts.infrastructure import LocalArtifactStore
from squid.schematics.application import ClaimedRenderJob, SchematicPreviewService, SchematicRenderJobService
from squid.schematics.application.previews import (
    CachedRender,
    FreshRender,
    PreviewObjectReservation,
    RenderPreparation,
    RenderSkipReason,
    SkippedRender,
    StoredRender,
)
from squid.worker.rendering import SchematicPreviewWorker
from squid_reactivity import LocalTopicBus, Topic

PNG = b"\x89PNG\r\n\x1a\npreview"
RECIPE_HASH = "a" * 64
OBJECT_KEY = f"schematic-renders/aa/{RECIPE_HASH}.png"
PUBLIC_URL = f"https://api.example/v1/schematic-renders/{RECIPE_HASH}/content"
BUILD_TOPIC = Topic("build", "7")


class MemoryRenderJobs:
    """Keep acknowledgement outcomes at the persistence boundary."""

    def __init__(self) -> None:
        self.job = ClaimedRenderJob(7, 0, uuid.uuid4())
        self.claims = 0
        self.completed: list[ClaimedRenderJob] = []
        self.failed: list[tuple[ClaimedRenderJob, str]] = []

    async def claim(self, *, limit: int) -> tuple[ClaimedRenderJob, ...]:
        self.claims += 1
        return (self.job,)[:limit]

    async def complete(self, job: ClaimedRenderJob) -> bool:
        self.completed.append(job)
        return True

    async def fail(self, job: ClaimedRenderJob, error: str, *, max_attempts: int) -> bool:
        self.failed.append((job, error))
        return job.attempts + 1 >= max_attempts


class PreparedPreviews(SchematicPreviewService):
    """Supply renderer decisions and record publication without a database or native engine."""

    def __init__(self) -> None:
        self.prepared: RenderPreparation | Exception = FreshRender(3, RECIPE_HASH, 768, 768, PNG)
        self.current = True
        self.publication_failures: list[Exception] = []
        self.upload_required = True
        self.recorded: list[tuple[FreshRender, str, str]] = []
        self.published_cached: list[CachedRender] = []
        self.published_urls: list[str] = []
        self.reservations: list[PreviewObjectReservation] = []
        self.ready: list[PreviewObjectReservation] = []
        self.cleanup_calls: list[tuple[int, int]] = []

    @override
    async def prepare_render(self, build_id: int) -> RenderPreparation:
        if isinstance(self.prepared, Exception):
            raise self.prepared
        return self.prepared

    @override
    async def publish_fresh_preview(self, render: FreshRender, url: str, object_key: str) -> StoredRender | None:
        self.recorded.append((render, url, object_key))
        if self.publication_failures:
            raise self.publication_failures.pop(0)
        if not self.current:
            return None
        self.published_urls[:] = [url]
        return StoredRender(render.schematic_id, render.recipe_hash, url, render.width, render.height, len(render.png))

    @override
    async def reserve_preview_object(self, render: FreshRender, object_key: str) -> PreviewObjectReservation:
        reservation = PreviewObjectReservation(
            object_key,
            len(render.png),
            hashlib.sha256(render.png).hexdigest(),
            upload_required=self.upload_required,
        )
        self.reservations.append(reservation)
        return reservation

    @override
    async def mark_preview_object_ready(self, reservation: PreviewObjectReservation) -> None:
        self.ready.append(reservation)

    @override
    async def publish_cached_preview(self, render: CachedRender) -> bool:
        self.published_cached.append(render)
        if self.current:
            self.published_urls[:] = [render.url]
        return self.current

    @override
    async def cleanup_preview_objects(self, *, retention_hours: int = 24, limit: int = 50) -> int:
        self.cleanup_calls.append((retention_hours, limit))
        return 1


@dataclass
class PreviewScenario:
    jobs: MemoryRenderJobs
    schematics: PreparedPreviews
    artifacts: LocalArtifactStore
    topics: LocalTopicBus
    published: list[Topic]
    directory: Path

    def worker(self, *, enabled: bool = True) -> SchematicPreviewWorker:
        return SchematicPreviewWorker(
            SchematicRenderJobService(self.jobs),
            self.schematics,
            self.artifacts,
            "https://api.example/" if enabled else None,
            enabled=enabled,
            topics=self.topics,
        )


@pytest.fixture
def preview(tmp_path: Path) -> Iterator[PreviewScenario]:
    topics = LocalTopicBus()
    published: list[Topic] = []
    unsubscribe = topics.subscribe(BUILD_TOPIC, published.append)
    try:
        yield PreviewScenario(
            MemoryRenderJobs(), PreparedPreviews(), LocalArtifactStore(tmp_path), topics, published, tmp_path
        )
    finally:
        unsubscribe()


async def test_fresh_render_is_stored_published_and_notifies_build_subscribers(preview: PreviewScenario) -> None:
    await preview.worker().process_batch()

    assert await preview.artifacts.get(OBJECT_KEY, max_bytes=len(PNG)) == PNG
    assert len(preview.schematics.reservations) == 1
    assert preview.schematics.ready == preview.schematics.reservations
    assert preview.schematics.recorded == [(preview.schematics.prepared, PUBLIC_URL, OBJECT_KEY)]
    assert preview.jobs.completed == [preview.jobs.job]
    assert preview.jobs.failed == []
    assert preview.published == [BUILD_TOPIC]


async def test_ready_reserved_object_is_reused_without_another_upload(preview: PreviewScenario) -> None:
    await preview.artifacts.put(OBJECT_KEY, PNG, content_type="image/png")
    stored = preview.directory / OBJECT_KEY
    identity = stored.stat().st_ino
    preview.schematics.upload_required = False

    await preview.worker().process_batch()

    assert stored.stat().st_ino == identity
    assert stored.read_bytes() == PNG
    assert preview.schematics.ready == []
    assert preview.schematics.published_urls == [PUBLIC_URL]
    assert preview.jobs.completed == [preview.jobs.job]


async def test_render_failure_is_released_for_retry(preview: PreviewScenario) -> None:
    preview.schematics.prepared = RuntimeError("renderer unavailable")

    await preview.worker().process_batch()

    assert preview.jobs.failed == [(preview.jobs.job, "renderer unavailable")]
    assert preview.jobs.completed == []
    assert preview.published == []


async def test_failure_after_object_upload_retries_the_same_recipe_and_publishes(preview: PreviewScenario) -> None:
    preview.schematics.publication_failures = [RuntimeError("database unavailable after upload")]
    worker = preview.worker()

    await worker.process_batch()
    assert await preview.artifacts.get(OBJECT_KEY, max_bytes=len(PNG)) == PNG
    assert preview.schematics.published_urls == []
    await worker.process_batch()

    assert await preview.artifacts.get(OBJECT_KEY, max_bytes=len(PNG)) == PNG
    assert preview.jobs.failed == [(preview.jobs.job, "database unavailable after upload")]
    assert preview.jobs.completed == [preview.jobs.job]
    assert preview.schematics.published_urls == [PUBLIC_URL]
    assert preview.published == [BUILD_TOPIC]


async def test_exhausted_publication_failure_leaves_reserved_object_for_cleanup(preview: PreviewScenario) -> None:
    preview.jobs.job = ClaimedRenderJob(7, 4, uuid.uuid4())
    preview.schematics.publication_failures = [RuntimeError("database unavailable after upload")]
    worker = preview.worker()

    await worker.process_batch()
    await worker.cleanup(retention_hours=24, limit=50)

    assert preview.jobs.failed == [(preview.jobs.job, "database unavailable after upload")]
    assert await preview.artifacts.get(OBJECT_KEY, max_bytes=len(PNG)) == PNG
    assert preview.schematics.ready == preview.schematics.reservations
    assert preview.schematics.cleanup_calls == [(24, 50)]


async def test_replaced_primary_cannot_publish_its_completed_render(preview: PreviewScenario) -> None:
    preview.schematics.current = False
    worker = preview.worker()

    await worker.process_batch()
    await worker.cleanup(retention_hours=24, limit=50)

    assert preview.schematics.published_urls == []
    assert preview.schematics.ready == preview.schematics.reservations
    assert preview.schematics.cleanup_calls == [(24, 50)]
    assert preview.jobs.completed == [preview.jobs.job]


async def test_cached_render_is_rechecked_before_publication(preview: PreviewScenario) -> None:
    prepared = CachedRender(3, RECIPE_HASH, 768, 768, PUBLIC_URL)
    preview.schematics.prepared = prepared
    preview.schematics.current = False

    await preview.worker().process_batch()

    assert preview.schematics.published_cached == [prepared]
    assert preview.schematics.published_urls == []
    assert list(preview.directory.iterdir()) == []
    assert preview.jobs.completed == [preview.jobs.job]


@pytest.mark.parametrize("reason", list(RenderSkipReason))
async def test_a_permanent_skip_acknowledges_without_upload_or_notification(
    preview: PreviewScenario, reason: RenderSkipReason
) -> None:
    preview.schematics.prepared = SkippedRender(reason)

    await preview.worker().process_batch()

    assert list(preview.directory.iterdir()) == []
    assert preview.schematics.recorded == []
    assert preview.schematics.published_cached == []
    assert preview.jobs.completed == [preview.jobs.job]
    assert preview.jobs.failed == []
    assert preview.published == []


async def test_disabled_rendering_leaves_durable_intents_unclaimed(preview: PreviewScenario) -> None:
    await preview.worker(enabled=False).process_batch()

    assert preview.jobs.claims == 0
    assert preview.jobs.completed == []
    assert preview.jobs.failed == []


async def test_preview_cleanup_delegates_reference_safe_retention_policy(preview: PreviewScenario) -> None:
    removed = await preview.worker().cleanup(retention_hours=48, limit=7)

    assert removed == 1
    assert preview.schematics.cleanup_calls == [(48, 7)]
