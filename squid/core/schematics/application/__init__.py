"""Public application API for schematics."""

from squid.core.schematics.application.attachments import SchematicPublication, StoredSchematic
from squid.core.schematics.application.commands import ConvertRequest, IngestRequest, RenderRequest, SimulationRequest
from squid.core.schematics.application.duplicates import DuplicateCandidate
from squid.core.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobRepository,
    SchematicJobService,
    SchematicJobSnapshot,
)
from squid.core.schematics.application.ports import (
    SchematicAnalyzer,
    SchematicPreviewPublisher,
    SchematicRepository,
    SchematicVersionResolver,
)
from squid.core.schematics.application.preview_service import SchematicPreviewService
from squid.core.schematics.application.previews import (
    CachedRender,
    FreshRender,
    RenderedSchematic,
    RenderPreparation,
    RenderSkipReason,
    SkippedRender,
)
from squid.core.schematics.application.render_jobs import ClaimedRenderJob, SchematicRenderJobService
from squid.core.schematics.application.services import (
    IngestedSchematic,
    SchematicService,
    summarise_losses,
)
from squid.core.schematics.domain.values import RgbaColor, VerifiedResourcePack

__all__ = [
    "CachedRender",
    "ClaimedRenderJob",
    "ClaimedSchematicJob",
    "ConvertRequest",
    "DuplicateCandidate",
    "FreshRender",
    "IngestRequest",
    "IngestedSchematic",
    "RenderPreparation",
    "RenderRequest",
    "RenderSkipReason",
    "RenderedSchematic",
    "RgbaColor",
    "SchematicAnalyzer",
    "SchematicJobErrorKind",
    "SchematicJobOperation",
    "SchematicJobRepository",
    "SchematicJobService",
    "SchematicJobSnapshot",
    "SchematicPreviewPublisher",
    "SchematicPreviewService",
    "SchematicPublication",
    "SchematicRenderJobService",
    "SchematicRepository",
    "SchematicService",
    "SchematicVersionResolver",
    "SimulationRequest",
    "SkippedRender",
    "StoredSchematic",
    "VerifiedResourcePack",
    "summarise_losses",
]
