"""Durable worker-owned schematic render projection."""

import logging

from squid.artifacts import ArtifactStore
from squid.builds.application import BuildEditPatch, BuildService
from squid.schematics.application import SchematicRenderJobService, SchematicService

logger = logging.getLogger(__name__)


class SchematicRenderProjector:
    """Render, publish, and project previews from durable build intents."""

    def __init__(
        self,
        jobs: SchematicRenderJobService,
        schematics: SchematicService,
        artifacts: ArtifactStore,
        builds: BuildService,
        public_base_url: str | None,
        *,
        enabled: bool,
    ) -> None:
        self._jobs = jobs
        self._schematics = schematics
        self._artifacts = artifacts
        self._builds = builds
        self._public_base_url = public_base_url.rstrip("/") if public_base_url is not None else None
        self._enabled = enabled

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
                        extra={"squid.build.id": job.build_id},
                    )
                continue
            await self._jobs.complete(job)

    async def _project(self, build_id: int) -> None:
        prepared = await self._schematics.prepare_render(build_id)
        if prepared is None:
            return
        url = prepared.cached_url
        if url is None:
            if self._public_base_url is None:
                msg = "Schematic rendering requires a public API base URL."
                raise RuntimeError(msg)
            assert prepared.png is not None
            object_key = f"schematic-renders/{prepared.recipe_hash[:2]}/{prepared.recipe_hash}.png"
            metadata = await self._artifacts.put(object_key, prepared.png, content_type="image/png")
            if metadata.byte_size != len(prepared.png):
                msg = "Object storage did not confirm the rendered preview size."
                raise RuntimeError(msg)
            url = f"{self._public_base_url}/v1/schematic-renders/{prepared.recipe_hash}/content"
            await self._schematics.record_render(prepared, url, object_key)

        build = await self._builds.get(build_id)
        if build is None or url in build.render_urls:
            return
        patch = BuildEditPatch(render_urls=[*build.render_urls, url])
        async with self._builds.edit(build_id, patch, blocking=True) as lease:
            await lease.commit()
