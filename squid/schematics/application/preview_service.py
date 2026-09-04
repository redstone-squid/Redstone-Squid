"""Application orchestration for generated schematic previews."""

import contextlib
import dataclasses
import hashlib
import json
import logging
from collections.abc import Generator

from whenever import Instant

from squid.core.errors import SquidError
from squid.schematics.application.attachments import StoredSchematic
from squid.schematics.application.commands import RenderRequest
from squid.schematics.application.ports import (
    SchematicAnalyzer,
    SchematicPreviewPublisher,
    SchematicResourcePackProvider,
    SchematicStore,
)
from squid.schematics.application.previews import (
    CachedRender,
    FreshRender,
    PreviewObjectReservation,
    RenderedSchematic,
    RenderPreparation,
    RenderSkipReason,
    SkippedRender,
    StoredRender,
)
from squid.schematics.domain.values import VerifiedResourcePack
from squid.schematics.errors import (
    SchematicNotFoundError,
    SchematicRenderRefusedError,
    SchematicRenderUnavailableError,
    SchematicSupportUnavailableError,
    SchematicWorkerCrashedError,
)

logger = logging.getLogger(__name__)

_MAX_RENDER_CONTENT_BYTES = 8 * 1024 * 1024


class SchematicPreviewService:
    """Prepare, render, publish, and clean previews until `aclose` releases their provider."""

    def __init__(
        self,
        analyzer: SchematicAnalyzer,
        store: SchematicStore,
        publisher: SchematicPreviewPublisher,
        *,
        engine_installed: bool,
        poisoned: set[str],
        render_enabled: bool,
        resource_pack: SchematicResourcePackProvider | None,
        render_request: RenderRequest | None,
        render_max_block_count: int,
        render_max_bounding_volume: int,
    ) -> None:
        self._analyzer = analyzer
        self._store = store
        self._publisher = publisher
        self._available = engine_installed
        self._poisoned = poisoned
        self._render_enabled = render_enabled
        self._resource_pack = resource_pack
        self._render_request = render_request or RenderRequest()
        self._render_max_block_count = render_max_block_count
        self._render_max_bounding_volume = render_max_bounding_volume
        self._warning_emitted = False

    async def prepare_render(self, build_id: int) -> RenderPreparation:
        """Decide whether durable work should use fresh bytes, a cache, or a permanent skip."""
        if not self._render_enabled:
            return SkippedRender(RenderSkipReason.RENDERING_DISABLED)
        stored = await self._store.get_featured(build_id)
        if stored is None:
            return SkippedRender(RenderSkipReason.NO_PRIMARY_SCHEMATIC)
        if reason := self._render_skip_reason(stored):
            self._log_render_skip(stored, reason)
            return SkippedRender(reason)
        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            self._log_render_skip(stored, RenderSkipReason.MISSING_FILE)
            return SkippedRender(RenderSkipReason.MISSING_FILE)

        resource_pack = await self._render_resources()
        recipe_hash = _render_recipe_hash(stored, self._render_request, resource_pack.sha256)
        cached = await self._publisher.get_render(stored.id, recipe_hash)
        if cached is not None:
            return CachedRender(
                schematic_id=stored.id,
                recipe_hash=recipe_hash,
                width=cached.width,
                height=cached.height,
                url=cached.url,
            )
        png = await self._render_png(stored, data, resource_pack, self._render_request)
        return FreshRender(
            schematic_id=stored.id,
            recipe_hash=recipe_hash,
            width=self._render_request.width,
            height=self._render_request.height,
            png=png,
        )

    def render_recipe(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        yaw: float | None = None,
        pitch: float | None = None,
        zoom: float | None = None,
    ) -> RenderRequest:
        """Return configured framing with caller overrides applied."""
        base = self._render_request
        return dataclasses.replace(
            base,
            width=base.width if width is None else width,
            height=base.height if height is None else height,
            yaw=base.yaw if yaw is None else yaw,
            pitch=base.pitch if pitch is None else pitch,
            zoom=base.zoom if zoom is None else zoom,
        )

    async def render_now(self, build_id: int, *, request: RenderRequest | None = None) -> RenderedSchematic:
        """Render the featured attachment for a waiting caller without publishing it."""
        if not self._available:
            raise SchematicSupportUnavailableError
        if not self._render_enabled:
            msg = "Schematic rendering is disabled on this instance."
            raise SchematicRenderUnavailableError(
                msg,
                developer_action="Set render_enabled (SQUID_SCHEMATIC_RENDER_ENABLED=true) and configure a "
                "resource pack to enable it.",
            )
        stored = await self._store.get_featured(build_id)
        if stored is None:
            raise SchematicNotFoundError(context={"build_id": build_id}, public_context={"build_id": build_id})
        if reason := self._render_skip_reason(stored):
            raise _refused(reason)

        render_request = request or self._render_request
        resource_pack = await self._render_resources()
        recipe_hash = _render_recipe_hash(stored, render_request, resource_pack.sha256)
        if await self._publisher.get_render(stored.id, recipe_hash) is not None:
            cached = await self._publisher.get_render_content(recipe_hash, max_bytes=_MAX_RENDER_CONTENT_BYTES)
            if cached is not None:
                return RenderedSchematic(
                    build_id=stored.build_id,
                    schematic_id=stored.id,
                    recipe_hash=recipe_hash,
                    width=render_request.width,
                    height=render_request.height,
                    png=cached,
                    from_cache=True,
                )
        data = await self._store.get_file(stored.file_sha256)
        if data is None:
            raise SchematicNotFoundError(context={"sha256": stored.file_sha256})
        png = await self._render_png(stored, data, resource_pack, render_request)
        return RenderedSchematic(
            build_id=stored.build_id,
            schematic_id=stored.id,
            recipe_hash=recipe_hash,
            width=render_request.width,
            height=render_request.height,
            png=png,
            from_cache=False,
        )

    async def explain_render_skip(self, stored: StoredSchematic) -> RenderSkipReason | None:
        """Return a permanent preview refusal without starting renderer I/O."""
        if not self._render_enabled:
            return RenderSkipReason.RENDERING_DISABLED
        if reason := self._render_skip_reason(stored):
            return reason
        if await self._store.get_file(stored.file_sha256) is None:
            return RenderSkipReason.MISSING_FILE
        return None

    async def publish_fresh_preview(self, render: FreshRender, url: str, object_key: str) -> StoredRender | None:
        """Persist and publish fresh bytes only while their source remains featured."""
        return await self._publisher.publish_fresh_preview(
            render.schematic_id,
            render.recipe_hash,
            url,
            object_key,
            width=render.width,
            height=render.height,
            byte_size=len(render.png),
        )

    async def reserve_preview_object(self, render: FreshRender, object_key: str) -> PreviewObjectReservation:
        """Reserve durable ownership before the transport uploads generated preview bytes."""
        return await self._publisher.reserve_preview_object(
            object_key,
            byte_size=len(render.png),
            sha256=hashlib.sha256(render.png).hexdigest(),
        )

    async def mark_preview_object_ready(self, reservation: PreviewObjectReservation) -> None:
        """Make a verified upload eligible for generated-link publication."""
        await self._publisher.mark_preview_object_ready(reservation)

    async def cleanup_preview_objects(self, *, retention_hours: int = 24, limit: int = 50) -> int:
        """Reclaim old generated objects only after all durable references disappear."""
        return await self._publisher.cleanup_unreferenced_preview_objects(
            older_than=Instant.now().subtract(hours=retention_hours),
            limit=limit,
        )

    async def publish_cached_preview(self, render: CachedRender) -> bool:
        """Publish cached bytes only while their source remains featured and ready."""
        return await self._publisher.publish_cached_preview(render.schematic_id, render.recipe_hash, render.url)

    async def render_content(self, recipe_hash: str, *, max_bytes: int = _MAX_RENDER_CONTENT_BYTES) -> bytes:
        """Return one registered PNG preview from private object storage."""
        content = await self._publisher.get_render_content(recipe_hash, max_bytes=max_bytes)
        if content is None:
            raise SchematicNotFoundError(context={"recipe_hash": recipe_hash})
        return content

    async def aclose(self) -> None:
        """Release the resource-pack provider owned by this preview service."""
        if self._resource_pack is not None:
            await self._resource_pack.aclose()

    async def _render_resources(self) -> VerifiedResourcePack:
        if self._resource_pack is None or not self._available:
            msg = "Schematic rendering is enabled but its worker resources are unavailable."
            raise SchematicRenderUnavailableError(msg)
        try:
            capabilities = await self._analyzer.capabilities()
            if not capabilities.can_render:
                msg = capabilities.unavailable_reason or "The schematic engine has no rendering adapter."
                raise SchematicRenderUnavailableError(msg)
            return await self._resource_pack.load()
        except SquidError:
            self._warn_once("The configured schematic resource pack is unavailable.", exc_info=True)
            raise

    def _render_skip_reason(self, stored: StoredSchematic) -> RenderSkipReason | None:
        if not stored.publication.is_sanitized:
            return RenderSkipReason.NOT_SANITIZED
        if stored.file_sha256 in self._poisoned:
            return RenderSkipReason.POISONED_FILE
        metrics = stored.analysis.metrics
        if metrics.block_count > self._render_max_block_count:
            return RenderSkipReason.OVER_BLOCK_BUDGET
        if metrics.bounding_volume > self._render_max_bounding_volume:
            return RenderSkipReason.OVER_VOLUME_BUDGET
        return None

    async def _render_png(
        self,
        stored: StoredSchematic,
        data: bytes,
        resource_pack: VerifiedResourcePack,
        request: RenderRequest,
    ) -> bytes:
        with self._quarantining(stored):
            try:
                png = await self._analyzer.render(data, request=request, resource_pack=resource_pack)
            except SchematicWorkerCrashedError:
                raise
            except SquidError:
                logger.warning(
                    "Could not render the schematic for build %s; the durable queue will retry.",
                    stored.build_id,
                    exc_info=True,
                    extra=_log_fields(stored),
                )
                raise
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            logger.warning(
                "The schematic renderer returned a non-PNG payload for build %s.",
                stored.build_id,
                extra=_log_fields(stored),
            )
            msg = "The schematic renderer returned an invalid image."
            raise SchematicRenderUnavailableError(
                msg,
                developer_action="This indicates an engine bug rather than a configuration problem; "
                "check the render worker logs.",
            )
        return png

    @contextlib.contextmanager
    def _quarantining(self, stored: StoredSchematic) -> Generator[None]:
        try:
            yield
        except SchematicWorkerCrashedError:
            self._poisoned.add(stored.file_sha256)
            logger.warning(
                "The schematic worker crashed during render; the file is quarantined on this instance.",
                extra=_log_fields(stored),
            )
            raise

    def _log_render_skip(self, stored: StoredSchematic, reason: RenderSkipReason) -> None:
        logger.info(
            "Skipping the render for schematic %s: %s.",
            stored.id,
            reason.value,
            extra={**_log_fields(stored), "squid.schematic.render_skip_reason": reason.value},
        )

    def _warn_once(self, message: str, *, exc_info: bool = False) -> None:
        if self._warning_emitted:
            return
        self._warning_emitted = True
        logger.warning(message, exc_info=exc_info)


def _render_recipe_hash(stored: StoredSchematic, request: RenderRequest, pack_sha256: str) -> str:
    dimensions = stored.analysis.metrics.dimensions
    recipe = {
        "file_sha256": stored.file_sha256,
        "pack_sha256": pack_sha256,
        "request": request.recipe_fields(),
        "schematic_dimensions": [dimensions.width, dimensions.height, dimensions.length],
        "analyzer_version": stored.analysis.analyzer_version,
    }
    encoded = json.dumps(recipe, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _refused(reason: RenderSkipReason) -> SchematicRenderRefusedError:
    return SchematicRenderRefusedError(reason.value, reason.description)


def _log_fields(stored: StoredSchematic) -> dict[str, str | int]:
    return {
        "squid.build.id": stored.build_id,
        "squid.schematic.format": stored.analysis.metrics.source_format.value,
        "squid.schematic.operation": "render",
    }
