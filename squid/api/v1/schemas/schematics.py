"""Public schematic metadata representations."""

from typing import Self

from pydantic import ConfigDict

from squid.api.v1.schemas import FromDomain
from squid.schematics.application.queries import StoredSchematic
from squid.schematics.domain import SchematicDimensions


class SchematicSize(FromDomain[SchematicDimensions]):
    """A schematic bounding-box size."""

    model_config = ConfigDict(extra="forbid")

    width: int
    height: int
    depth: int

    @classmethod
    def from_domain(cls, dimensions: SchematicDimensions, /) -> Self:
        return cls(width=dimensions.width, height=dimensions.height, depth=dimensions.length)


class SchematicSummary(FromDomain[StoredSchematic]):
    """Allowlisted analysis metadata for one stored build schematic."""

    model_config = ConfigDict(extra="forbid")

    id: int
    primary: bool
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
    license: str
    license_url: str
    download_url: str

    @classmethod
    def from_domain(cls, schematic: StoredSchematic, /) -> Self:
        analysis = schematic.analysis
        metrics = analysis.metrics
        license = schematic.publication.license
        if not schematic.publication.is_public_downloadable or license is None:
            msg = "Only public downloadable schematics can be rendered in the public API."
            raise ValueError(msg)
        return cls(
            id=schematic.id,
            primary=schematic.is_primary,
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
            license=license.value,
            license_url=license.uri,
            download_url=f"/v1/builds/{schematic.build_id}/schematics/{schematic.id}/content",
        )
