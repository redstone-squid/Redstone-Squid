"""Durable schematic render projection tests."""

from typing import Any, cast

from whenever import Instant

from squid.artifacts import ArtifactMetadata
from squid.builds.domain import Build
from squid.schematics.application import ClaimedRenderJob
from squid.schematics.application.queries import PreparedRender
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
    def __init__(self, prepared: PreparedRender | Exception) -> None:
        self.prepared = prepared
        self.recorded: list[tuple[PreparedRender, str, str]] = []

    async def prepare_render(self, _build_id: int) -> PreparedRender | None:
        if isinstance(self.prepared, Exception):
            raise self.prepared
        return self.prepared

    async def record_render(self, render: PreparedRender, url: str, object_key: str) -> None:
        self.recorded.append((render, url, object_key))


class FakeArtifacts:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
        self.puts.append((key, data, content_type))
        return ArtifactMetadata(byte_size=len(data))


class FakeLease:
    def __init__(self) -> None:
        self.committed = False

    async def __aenter__(self) -> "FakeLease":
        return self

    async def commit(self) -> None:
        self.committed = True

    async def __aexit__(self, *_exc: object) -> None:
        pass


class FakeBuilds:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.patches: list[Any] = []
        self.lease = FakeLease()

    async def get(self, _build_id: int) -> Build:
        return self.build

    def edit(self, _build_id: int, patch: Any, *, blocking: bool) -> FakeLease:
        assert blocking is True
        self.patches.append(patch)
        return self.lease


async def test_fresh_render_is_published_and_projected_onto_build() -> None:
    job = ClaimedRenderJob(7, 0, Instant.now())
    jobs = FakeJobs(job)
    schematics = FakeSchematics(PreparedRender(3, RECIPE_HASH, 768, 768, png=PNG))
    artifacts = FakeArtifacts()
    builds = FakeBuilds(Build(id=7))
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, schematics),
        cast(Any, artifacts),
        cast(Any, builds),
        "https://api.example/",
        enabled=True,
    )

    await projector.process_batch()

    object_key = f"schematic-renders/aa/{RECIPE_HASH}.png"
    public_url = f"https://api.example/v1/schematic-renders/{RECIPE_HASH}/content"
    assert artifacts.puts == [(object_key, PNG, "image/png")]
    assert schematics.recorded == [(schematics.prepared, public_url, object_key)]
    assert builds.patches[0].render_urls == [public_url]
    assert builds.lease.committed is True
    assert jobs.completed == [job]
    assert jobs.failed == []


async def test_render_failure_is_released_for_retry() -> None:
    job = ClaimedRenderJob(7, 0, Instant.now())
    jobs = FakeJobs(job)
    error = RuntimeError("renderer unavailable")
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, FakeSchematics(error)),
        cast(Any, FakeArtifacts()),
        cast(Any, FakeBuilds(Build(id=7))),
        "https://api.example",
        enabled=True,
    )

    await projector.process_batch()

    assert jobs.failed == [(job, error)]
    assert jobs.completed == []


async def test_disabled_rendering_leaves_durable_intents_unclaimed() -> None:
    job = ClaimedRenderJob(7, 0, Instant.now())
    jobs = FakeJobs(job)
    projector = SchematicRenderProjector(
        cast(Any, jobs),
        cast(Any, FakeSchematics(RuntimeError("must not render"))),
        cast(Any, FakeArtifacts()),
        cast(Any, FakeBuilds(Build(id=7))),
        None,
        enabled=False,
    )

    await projector.process_batch()

    assert jobs.completed == []
    assert jobs.failed == []
