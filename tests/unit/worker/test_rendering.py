"""Durable schematic render projection tests."""

import uuid
from typing import Any, cast

import pytest
from squid_reactive import Topic

from squid.artifacts import ArtifactMetadata
from squid.schematics.application import ClaimedRenderJob
from squid.schematics.application.queries import (
    CachedRender,
    FreshRender,
    RenderPreparation,
    RenderSkipReason,
    SkippedRender,
)
from squid.worker.rendering import SchematicRenderProjector

PNG = b"\x89PNG\r\n\x1a\npreview"
RECIPE_HASH = "a" * 64


class FakeJobs:
    def __init__(self, job: ClaimedRenderJob) -> None:
        self.job = job
        self.completed: list[ClaimedRenderJob] = []
        self.failed: list[tuple[ClaimedRenderJob, Exception]] = []

    async def claim(self) -> tuple[ClaimedRenderJob, ...]:
        return (self.job,)

    async def complete(self, job: ClaimedRenderJob) -> bool:
        self.completed.append(job)
        return True

    async def fail(self, job: ClaimedRenderJob, error: Exception) -> bool:
        self.failed.append((job, error))
        return False


class FakeSchematics:
    def __init__(self, prepared: RenderPreparation | Exception, *, current: bool = True) -> None:
        self.prepared = prepared
        self.current = current
        self.recorded: list[tuple[FreshRender, str, str]] = []
        self.projected: list[CachedRender] = []
        self.published_urls: list[str] = []

    async def prepare_render(self, _build_id: int) -> RenderPreparation:
        if isinstance(self.prepared, Exception):
            raise self.prepared
        return self.prepared

    async def record_render(self, render: FreshRender, url: str, object_key: str) -> object | None:
        self.recorded.append((render, url, object_key))
        if not self.current:
            return None
        self.published_urls[:] = [url]
        return object()

    async def project_render(self, render: CachedRender) -> bool:
        self.projected.append(render)
        if self.current:
            self.published_urls[:] = [render.url]
        return self.current


class FakeArtifacts:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
        self.puts.append((key, data, content_type))
        return ArtifactMetadata(byte_size=len(data))


async def test_fresh_render_is_published_and_projected_onto_build() -> None:
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    jobs = FakeJobs(job)
    schematics = FakeSchematics(FreshRender(3, RECIPE_HASH, 768, 768, PNG))
    artifacts = FakeArtifacts()
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, schematics),
        cast(Any, artifacts),
        "https://api.example/",
        enabled=True,
    )

    await projector.process_batch()

    object_key = f"schematic-renders/aa/{RECIPE_HASH}.png"
    public_url = f"https://api.example/v1/schematic-renders/{RECIPE_HASH}/content"
    assert artifacts.puts == [(object_key, PNG, "image/png")]
    assert schematics.recorded == [(schematics.prepared, public_url, object_key)]
    assert jobs.completed == [job]
    assert jobs.failed == []


async def test_render_failure_is_released_for_retry() -> None:
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    jobs = FakeJobs(job)
    error = RuntimeError("renderer unavailable")
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, FakeSchematics(error)),
        cast(Any, FakeArtifacts()),
        "https://api.example",
        enabled=True,
    )

    await projector.process_batch()

    assert jobs.failed == [(job, error)]
    assert jobs.completed == []


async def test_replaced_primary_cannot_publish_its_completed_render() -> None:
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    jobs = FakeJobs(job)
    schematics = FakeSchematics(FreshRender(3, RECIPE_HASH, 768, 768, PNG), current=False)
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, schematics),
        cast(Any, FakeArtifacts()),
        "https://api.example",
        enabled=True,
    )

    await projector.process_batch()

    assert schematics.published_urls == []
    assert jobs.completed == [job]


async def test_cached_render_is_rechecked_before_projection() -> None:
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    jobs = FakeJobs(job)
    cached_url = f"https://api.example/v1/schematic-renders/{RECIPE_HASH}/content"
    prepared = CachedRender(3, RECIPE_HASH, 768, 768, cached_url)
    schematics = FakeSchematics(prepared, current=False)
    artifacts = FakeArtifacts()
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, schematics),
        cast(Any, artifacts),
        "https://api.example",
        enabled=True,
    )

    await projector.process_batch()

    assert schematics.projected == [prepared]
    assert schematics.published_urls == []
    assert artifacts.puts == []
    assert jobs.completed == [job]


@pytest.mark.parametrize("reason", list(RenderSkipReason))
async def test_a_permanent_skip_acknowledges_the_intent_instead_of_retrying(reason: RenderSkipReason) -> None:
    """Retrying a build that will never render only burns the job's attempts."""
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    jobs = FakeJobs(job)
    schematics = FakeSchematics(SkippedRender(reason))
    artifacts = FakeArtifacts()
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, schematics),
        cast(Any, artifacts),
        "https://api.example",
        enabled=True,
    )

    await projector.process_batch()

    assert artifacts.puts == []
    assert schematics.recorded == []
    assert schematics.projected == []
    assert jobs.completed == [job]
    assert jobs.failed == []


async def test_disabled_rendering_leaves_durable_intents_unclaimed() -> None:
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    jobs = FakeJobs(job)
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, FakeSchematics(RuntimeError("must not render"))),
        cast(Any, FakeArtifacts()),
        None,
        enabled=False,
    )

    await projector.process_batch()

    assert jobs.completed == []
    assert jobs.failed == []


class FakeTopics:
    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, *topics: object) -> None:
        self.published.extend(topics)


async def test_a_finished_render_publishes_the_builds_resource_topic() -> None:
    """The panels live in the bot process, so the render has to say it landed."""
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    jobs = FakeJobs(job)
    topics = FakeTopics()
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, FakeSchematics(FreshRender(3, RECIPE_HASH, 768, 768, PNG))),
        cast(Any, FakeArtifacts()),
        "https://api.example",
        enabled=True,
        topics=topics,
    )

    await projector.process_batch()

    assert topics.published == [Topic("build", "7")]


async def test_a_skipped_render_changes_nothing_to_publish() -> None:
    job = ClaimedRenderJob(7, 0, uuid.uuid4())
    topics = FakeTopics()
    projector = SchematicRenderProjector(
        cast(Any, FakeJobs(job)),
        cast(Any, FakeSchematics(SkippedRender(RenderSkipReason.NO_PRIMARY_SCHEMATIC))),
        cast(Any, FakeArtifacts()),
        "https://api.example",
        enabled=True,
        topics=topics,
    )

    await projector.process_batch()

    assert topics.published == []
