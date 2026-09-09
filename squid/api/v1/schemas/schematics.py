"""Public schematic metadata representations."""

from typing import Self

from pydantic import ConfigDict, Field

from squid.api.v1.schemas import FromDomain
from squid.schematics.application.queries import StoredSchematic
from squid.schematics.domain import SchematicDimensions


class SchematicSize(FromDomain[SchematicDimensions]):
    """A schematic bounding-box size, in blocks along each axis."""

    model_config = ConfigDict(extra="forbid")

    width: int
    height: int
    depth: int

    @classmethod
    def from_domain(cls, dimensions: SchematicDimensions, /) -> Self:
        return cls(width=dimensions.width, height=dimensions.height, depth=dimensions.length)


class SchematicSummary(FromDomain[StoredSchematic]):
    """Allowlisted analysis metadata for one stored build schematic.

    Only schematics the submitter published for download appear here, so every one carries a license.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    primary: bool = Field(description="Whether this is the build's main schematic.")
    format: str = Field(
        description="Container format of the stored file: `litematic`, `schem`, `schematic`, `nbt` or `mcstructure`."
    )
    byte_size: int = Field(description="Size of the stored file in bytes.")
    dimensions: SchematicSize = Field(description="Tight bounding box of the placed blocks.")
    allocated_dimensions: SchematicSize = Field(
        description="Region the file allocates, which the tight box can be much smaller than."
    )
    block_count: int = Field(
        description="Non-air blocks. Not the Door Rules cumulative volume, which counts air pockets and carries "
        "hallway, frame and hitbox exceptions."
    )
    bounding_volume: int = Field(description="Volume of `dimensions`, air included.")
    entity_count: int
    palette_size: int = Field(description="Distinct block states in the file's palette.")
    source_data_version: int | None = Field(
        description="Minecraft data version the file declares, null when its format records none."
    )
    analyzer_version: str = Field(
        description="Which analysis pass produced these metrics. Values from different versions are not comparable."
    )
    analysis_schema_version: int
    license: str = Field(description="Creative Commons license the submitter granted, such as `cc_by_4_0`.")
    license_url: str = Field(description="Canonical deed URI for `license`.")
    download_url: str = Field(description="Path on this API serving the file; relative to the API root, not absolute.")

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
