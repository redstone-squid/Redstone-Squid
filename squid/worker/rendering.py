"""Durable worker-owned schematic render projection."""

import logging

from squid.artifacts import ArtifactStore
from squid.diagnostics.log_capture import work_lost
from squid.schematics.application import SchematicRenderJobService, SchematicService
from squid.schematics.application.previews import CachedRender, FreshRender, SkippedRender
from squid.topics import TopicPublisher, resource_topic

logger = logging.getLogger(__name__)


class SchematicRenderProjector:
    """Render, publish, and project previews from durable build intents."""

    def __init__(
        self,
        jobs: SchematicRenderJobService,
        schematics: SchematicService,
        artifacts: ArtifactStore,
        public_base_url: str | None,
        *,
        enabled: bool,
        topics: TopicPublisher | None = None,
    ) -> None:
        self._jobs = jobs
        self._schematics = schematics
        self._artifacts = artifacts
        self._public_base_url = public_base_url.rstrip("/") if public_base_url is not None else None
        self._enabled = enabled
        self._topics = topics

    async def process_batch(self) -> None:
        """Process one bounded batch and retain exhausted work as dead letters."""
        if not self._enabled:
            return
        for job in await self._jobs.claim():
            try:
                await self._project(job.build_id)
            except Exception as error:
                dead = await self._jobs.fail(job, error)
                if dead:
                    logger.exception(
                        "Dead-lettered a schematic render projection",
                        extra={"squid.build.id": job.build_id, **work_lost()},
                    )
                continue
            await self._jobs.complete(job)

    async def _project(self, build_id: int) -> None:
        """Carry out whatever the service decided, letting only retryable failures escape.

        A `SkippedRender` is a permanent verdict for this build's recipe, so returning
        normally lets `process_batch` acknowledge the job instead of burning its attempts on
        a build that will never render.
        """
        match await self._schematics.prepare_render(build_id):
            case SkippedRender(reason=reason):
                logger.info(
                    "Skipped a schematic render projection",
                    extra={"squid.build.id": build_id, "squid.schematic.render_skip_reason": reason.value},
                )
                return
            case CachedRender() as cached:
                await self._schematics.project_render(cached)
            case FreshRender() as fresh:
                await self._publish(fresh)
        if self._topics is not None:
            # Panels showing this build re-read instead of waiting for a click; a render
            # that turned out to be superseded costs one no-op refresh.
            self._topics.publish(resource_topic("build", str(build_id)))

    async def _publish(self, render: FreshRender) -> None:
        """Upload a fresh preview and project the URL it is now reachable at."""
        if self._public_base_url is None:
            msg = "Schematic rendering requires a public API base URL."
            raise RuntimeError(msg)
        object_key = f"schematic-renders/{render.recipe_hash[:2]}/{render.recipe_hash}.png"
        metadata = await self._artifacts.put(object_key, render.png, content_type="image/png")
        if metadata.byte_size != len(render.png):
            msg = "Object storage did not confirm the rendered preview size."
            raise RuntimeError(msg)
        url = f"{self._public_base_url}/v1/schematic-renders/{render.recipe_hash}/content"
        await self._schematics.record_render(render, url, object_key)
