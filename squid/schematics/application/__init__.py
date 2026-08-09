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
from squid.schematics.application.queries import DuplicateCandidate, StoredSchematic
from squid.schematics.application.services import (
    IngestedSchematic,
    SchematicService,
    SchematicStorageMaintenance,
    summarise_losses,
)

__all__ = [
    "ClaimedSchematicJob",
    "ConvertRequest",
    "DuplicateCandidate",
    "IngestRequest",
    "IngestedSchematic",
    "RenderRequest",
    "SchematicAnalyzer",
    "SchematicJobErrorKind",
    "SchematicJobOperation",
    "SchematicJobRepository",
    "SchematicJobService",
    "SchematicJobSnapshot",
    "SchematicService",
    "SchematicStorageMaintenance",
    "SchematicStore",
    "SchematicVersionResolver",
    "SimulationRequest",
    "StoredSchematic",
    "summarise_losses",
]
