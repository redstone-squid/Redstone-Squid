"""Assemble schematic services that submit work to the durable queue."""

from squid.config import SchematicConfig
from squid.core.artifacts import ArtifactStore
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.schematics.application import RenderRequest, SchematicJobService, SchematicService
from squid.core.schematics.application.preview_storage import SchematicPreviewStorage
from squid.core.schematics.application.storage import SchematicStorageService
from squid.core.schematics.application.video_service import SchematicVideoService
from squid.core.schematics.domain.models import SchematicLimits
from squid.core.schematics.infrastructure.durable import QueuedSchematicAnalyzer
from squid.core.schematics.infrastructure.resource_pack import ResourcePackLoader
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.versions.application.services import VersionService


def create_schematic_service(
    config: SchematicConfig,
    artifacts: ArtifactStore,
    jobs: SchematicJobService,
    *,
    uow_factory: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    render_capable: bool = False,
) -> SchematicService:
    """Build a schematic service that submits work to the durable queue.

    When `render_capable` and rendering are enabled, attach a resource-pack loader
    so this process can prepare render requests. Native rendering still runs in
    the dedicated worker process.
    """
    analyzer = QueuedSchematicAnalyzer(jobs, artifacts, config)
    resource_pack = None
    if render_capable and (config.render_enabled or config.video_enabled):
        resource_pack = ResourcePackLoader(
            path=config.render_pack_path,
            url=str(config.render_pack_url) if config.render_pack_url is not None else None,
            expected_sha256=config.render_pack_sha256,
            cache_dir=config.render_cache_dir,
        )
    previews = SchematicPreviewStorage(artifacts, uow_factory=uow_factory)
    store = SchematicStorageService(artifacts, uow_factory=uow_factory)
    return SchematicService(
        analyzer,
        store,
        previews,
        VersionService(uow_factory=uow_factory),
        limits=SchematicLimits(
            max_upload_bytes=config.max_upload_bytes,
            max_inflated_bytes=config.max_inflated_bytes,
            max_allocated_volume=config.max_allocated_volume,
            max_axis_length=config.max_axis_length,
        ),
        engine_installed=config.enabled,
        duplicate_metric_tolerance=config.duplicate_metric_tolerance,
        duplicate_near_distance=config.duplicate_near_distance,
        duplicate_max_comparisons=config.duplicate_max_comparisons,
        duplicate_result_limit=config.duplicate_result_limit,
        duplicate_total_timeout_seconds=config.duplicate_total_timeout_seconds,
        video_service=SchematicVideoService(
            jobs,
            artifacts,
            store,
            resource_pack,
            enabled=config.enabled and config.video_enabled and render_capable,
            max_blocks=config.render_max_block_count,
            max_volume=config.render_max_bounding_volume,
            public_base_url=str(config.render_public_base_url) if config.render_public_base_url else None,
        ),
        render_enabled=config.render_enabled and render_capable,
        resource_pack=resource_pack,
        render_request=RenderRequest(
            width=config.render_width,
            height=config.render_height,
            background=config.render_background,
        ),
        render_max_block_count=config.render_max_block_count,
        render_max_bounding_volume=config.render_max_bounding_volume,
    )
