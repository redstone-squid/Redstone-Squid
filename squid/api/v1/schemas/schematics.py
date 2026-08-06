"""Public schematic metadata representations."""

from pydantic import BaseModel, ConfigDict

from squid.schematics.application.queries import StoredSchematic
from squid.schematics.domain import SchematicDimensions


class SchematicSize(BaseModel):
    """A schematic bounding-box size."""

    model_config = ConfigDict(extra="forbid")

    width: int
    height: int
    depth: int

    @classmethod
    def from_domain(cls, dimensions: SchematicDimensions) -> "SchematicSize":
        return cls(width=dimensions.width, height=dimensions.height, depth=dimensions.length)


class SchematicSummary(BaseModel):
    """Allowlisted analysis metadata for one stored build schematic."""

    model_config = ConfigDict(extra="forbid")

    id: int
    sha256: str
    primary: bool
    filename: str | None
    format: str
    byte_size: int
    dimensions: SchematicSize
    allocated_dimensions: SchematicSize
    block_count: int
    bounding_volume: int
    entity_count: int
    palette_size: int
    source_data_version: int | None
    analyzer_version: str
    analysis_schema_version: int

    @classmethod
    def from_domain(cls, schematic: StoredSchematic) -> "SchematicSummary":
        analysis = schematic.analysis
        metrics = analysis.metrics
        return cls(
            id=schematic.id,
            sha256=schematic.file_sha256,
            primary=schematic.is_primary,
            filename=schematic.original_filename,
            format=metrics.source_format.value,
            byte_size=metrics.byte_size,
            dimensions=SchematicSize.from_domain(metrics.dimensions),
            allocated_dimensions=SchematicSize.from_domain(metrics.allocated_dimensions),
            block_count=metrics.block_count,
            bounding_volume=metrics.bounding_volume,
            entity_count=metrics.entity_count,
            palette_size=metrics.palette_size,
            source_data_version=metrics.source_data_version,
            analyzer_version=analysis.analyzer_version,
            analysis_schema_version=analysis.analysis_schema_version,
        )
