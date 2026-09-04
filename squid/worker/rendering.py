"""Durable worker-owned schematic preview publication."""

import hashlib
import logging

from squid.artifacts import ArtifactStore
from squid.diagnostics.log_capture import work_lost
from squid.schematics.application import SchematicRenderJobService, SchematicService
from squid.schematics.application.previews import CachedRender, FreshRender, SkippedRender
from squid.topics import TopicPublisher, resource_topic

logger = logging.getLogger(__name__)


class SchematicPreviewWorker:
    """Render and publish generated previews from durable build intents."""

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
                await self._publish_preview(job.build_id)
            except Exception as error:
                dead = await self._jobs.fail(job, error)
                if dead:
                    logger.exception(
                        "Dead-lettered schematic preview publication",
                        extra={"squid.build.id": job.build_id, **work_lost()},
                    )
                continue
            await self._jobs.complete(job)

    async def _publish_preview(self, build_id: int) -> None:
        """Carry out whatever the service decided, letting only retryable failures escape.

        A `SkippedRender` is a permanent verdict for this build's recipe, so returning
        normally lets `process_batch` acknowledge the job instead of burning its attempts on
        a build that will never render.
        """
        match await self._schematics.prepare_render(build_id):
            case SkippedRender(reason=reason):
                logger.info(
                    "Skipped schematic preview publication",
                    extra={"squid.build.id": build_id, "squid.schematic.render_skip_reason": reason.value},
                )
                return
            case CachedRender() as cached:
                await self._schematics.publish_cached_preview(cached)
            case FreshRender() as fresh:
                await self._upload_and_publish(fresh)
        if self._topics is not None:
            # Panels showing this build re-read instead of waiting for a click; a render
            # that turned out to be superseded costs one no-op refresh.
            self._topics.publish(resource_topic("build", str(build_id)))

    async def _upload_and_publish(self, render: FreshRender) -> None:
        """Upload a fresh preview and publish the URL it is now reachable at."""
        if self._public_base_url is None:
            msg = "Schematic rendering requires a public API base URL."
            raise RuntimeError(msg)
        object_key = f"schematic-renders/{render.recipe_hash[:2]}/{render.recipe_hash}.png"
        reservation = await self._schematics.reserve_preview_object(render, object_key)
        if reservation.upload_required:
            metadata = await self._artifacts.put(object_key, render.png, content_type="image/png")
            if metadata.byte_size != reservation.byte_size or metadata.sha256 not in (None, reservation.sha256):
                msg = "Object storage did not confirm the rendered preview bytes."
                raise RuntimeError(msg)
            if hashlib.sha256(render.png).hexdigest() != reservation.sha256:
                msg = "The generated preview changed after its object was reserved."
                raise RuntimeError(msg)
            await self._schematics.mark_preview_object_ready(reservation)
        url = f"{self._public_base_url}/v1/schematic-renders/{render.recipe_hash}/content"
        await self._schematics.publish_fresh_preview(render, url, object_key)

    async def cleanup(self, *, retention_hours: int = 24, limit: int = 50) -> int:
        """Run one bounded reference-safe generated-preview cleanup batch."""
        return await self._schematics.cleanup_preview_objects(retention_hours=retention_hours, limit=limit)
