"""Public application API for schematics."""

from squid.schematics.application.commands import ConvertRequest, IngestRequest, RenderRequest, SimulationRequest
from squid.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobRepository,
    SchematicJobService,
    SchematicJobSnapshot,
)
from squid.schematics.application.ports import SchematicAnalyzer, SchematicStore, SchematicVersionResolver
from squid.schematics.application.queries import (
    CachedRender,
    DuplicateCandidate,
    FreshRender,
    RenderedSchematic,
    RenderPreparation,
    RenderSkipReason,
    SchematicPublication,
    SkippedRender,
    StoredSchematic,
)
from squid.schematics.application.render_jobs import ClaimedRenderJob, SchematicRenderJobService
from squid.schematics.application.services import (
    IngestedSchematic,
    SchematicService,
    summarise_losses,
)
from squid.schematics.domain.values import RgbaColor, VerifiedResourcePack

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
    "SchematicPublication",
    "SchematicRenderJobService",
    "SchematicService",
    "SchematicStore",
    "SchematicVersionResolver",
    "SimulationRequest",
    "SkippedRender",
    "StoredSchematic",
    "VerifiedResourcePack",
    "summarise_losses",
]
