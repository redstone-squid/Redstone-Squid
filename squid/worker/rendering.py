"""Durable worker-owned schematic preview publication."""

import hashlib
import logging

from squid.core.actors import ActingContext
from squid.core.artifacts import ArtifactStore
from squid.core.diagnostics.log_capture import work_lost
from squid.core.posts.topics import TopicPublisher, resource_topic
from squid.core.schematics.application import SchematicPreviewService, SchematicRenderJobService
from squid.core.schematics.application.previews import CachedRender, FreshRender, SkippedRender
from squid.core.worker_context import worker_execution, worker_scope

logger = logging.getLogger(__name__)


class SchematicPreviewWorker:
    """Process queued requests to update a build's generated preview.

    SchematicPreviewService selects the current featured attachment and prepares fresh or cached
    output. This worker stores any new PNG, then asks the service to publish its URL on the build.
    A request can also finish without an image when preparation reports a skip or the attachment
    changes before publication.
    """

    def __init__(
        self,
        jobs: SchematicRenderJobService,
        schematics: SchematicPreviewService,
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

    async def process_batch(self, *, context: ActingContext) -> None:
        """Process one bounded batch and retain exhausted work as dead letters."""
        with worker_scope(context):
            if not self._enabled:
                return
            for job in await self._jobs.claim(context=ActingContext.current()):
                with worker_execution("schematic-renders", job.claim_token):
                    try:
                        await self._publish_preview(job.build_id)
                    except Exception as error:
                        dead = await self._jobs.fail(job, error, context=ActingContext.current())
                        if dead:
                            logger.exception(
                                "Dead-lettered schematic preview publication",
                                extra={"squid.build.id": job.build_id, **work_lost()},
                            )
                        continue
                    await self._jobs.complete(job, context=ActingContext.current())

    async def _publish_preview(self, build_id: int) -> None:
        """Apply the preparation decision, acknowledging skips without an upload.

        A skipped recipe returns normally. Preparation, storage, publication, and
        topic errors propagate to the batch's retry policy. A stale render may be
        declined by the publication service and still complete the job normally.
        """
        match await self._schematics.prepare_render(build_id, context=ActingContext.current()):
            case SkippedRender(reason=reason):
                logger.info(
                    "Skipped schematic preview publication",
                    extra={"squid.build.id": build_id, "squid.schematic.render_skip_reason": reason.value},
                )
                return
            case CachedRender() as cached:
                await self._schematics.publish_cached_preview(cached, context=ActingContext.current())
            case FreshRender() as fresh:
                await self._upload_and_publish(fresh)
        if self._topics is not None:
            # Panels showing this build re-read instead of waiting for a click; a render
            # that turned out to be superseded costs one no-op refresh.
            self._topics.publish(resource_topic("build", str(build_id)))

    async def _upload_and_publish(self, render: FreshRender) -> None:
        """Reserve storage, upload if needed, then offer the preview for publication.

        Uploaded bytes must match the reservation and returned storage metadata.
        The publication service checks whether the render still belongs to the
        build's current primary schematic.
        """
        if self._public_base_url is None:
            msg = "Schematic rendering requires a public API base URL."
            raise RuntimeError(msg)
        object_key = f"schematic-renders/{render.recipe_hash[:2]}/{render.recipe_hash}.png"
        reservation = await self._schematics.reserve_preview_object(render, object_key, context=ActingContext.current())
        if reservation.upload_required:
            metadata = await self._artifacts.put(object_key, render.png, content_type="image/png")
            if metadata.byte_size != reservation.byte_size or metadata.sha256 not in (None, reservation.sha256):
                msg = "Object storage did not confirm the rendered preview bytes."
                raise RuntimeError(msg)
            if hashlib.sha256(render.png).hexdigest() != reservation.sha256:
                msg = "The generated preview changed after its object was reserved."
                raise RuntimeError(msg)
            await self._schematics.mark_preview_object_ready(reservation, context=ActingContext.current())
        url = f"{self._public_base_url}/v1/schematic-renders/{render.recipe_hash}/content"
        await self._schematics.publish_fresh_preview(render, url, object_key, context=ActingContext.current())

    async def cleanup(self, *, context: ActingContext, retention_hours: int = 24, limit: int = 50) -> int:
        """Delete up to limit old preview objects that no stored render still references."""
        with worker_scope(context):
            return await self._schematics.cleanup_preview_objects(
                retention_hours=retention_hours, limit=limit, context=context
            )
