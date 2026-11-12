"""Public schematic metadata representations."""

from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from squid.api.v1.schemas import FromDomain
from squid.schematics.application.attachments import StoredSchematic
from squid.schematics.application.video import VideoInteraction, VideoRenderRequest
from squid.schematics.application.video_service import VideoStatus
from squid.schematics.domain import SchematicDimensions


class SchematicSize(FromDomain[SchematicDimensions]):
    """A schematic bounding-box size."""

    model_config = ConfigDict(extra="forbid")

    width: int
    height: int
    depth: int

    @classmethod
    @override
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
    @override
    def from_domain(cls, schematic: StoredSchematic, /, *, download_url: str | None = None) -> Self:
        """Render a published schematic with a URL obtained from the API router.

        Raise ValueError if publication is not allowed, its license is missing,
        or no download URL was supplied.
        """
        analysis = schematic.analysis
        metrics = analysis.metrics
        license = schematic.publication.license
        if not schematic.publication.is_public_downloadable or license is None:
            msg = "Only public downloadable schematics can be rendered in the public API."
            raise ValueError(msg)
        if download_url is None:
            msg = "Public schematic representations require a route-derived download URL."
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
            download_url=download_url,
        )


class VideoResponse(VideoStatus):
    status_url: str
    download_url: str | None = None

    @classmethod
    def from_status(cls, status: VideoStatus, *, status_url: str, download_url: str) -> Self:
        return cls(
            **status.model_dump(),
            status_url=status_url,
            download_url=download_url if status.status == "succeeded" else None,
        )


class VideoSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    interactions: tuple[VideoInteraction, ...] = (VideoInteraction(tick=20),)


class VideoCamera(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["fixed", "orbit"] = "orbit"
    yaw: float = Field(default=45, ge=-360, le=360)
    pitch: float = Field(default=30, ge=-90, le=90)
    zoom: float = Field(default=1, gt=0, le=16)
    orbit_degrees: float = Field(default=180, ge=-360, le=360)


class VideoCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(default=640, ge=64, le=1280, multiple_of=2, strict=True)
    height: int = Field(default=360, ge=64, le=720, multiple_of=2, strict=True)
    fps: int = Field(default=30, ge=1, le=30, strict=True)
    duration_ticks: int = Field(default=120, ge=1, le=200, strict=True)
    simulation: VideoSimulation = Field(default_factory=VideoSimulation)
    camera: VideoCamera = Field(default_factory=VideoCamera)

    @model_validator(mode="after")
    def validate_recipe(self) -> Self:
        self.to_request()
        return self

    def to_request(self) -> VideoRenderRequest:
        return VideoRenderRequest(
            width=self.width,
            height=self.height,
            fps=self.fps,
            duration_ticks=self.duration_ticks,
            simulation_enabled=self.simulation.enabled,
            interactions=(
                self.simulation.interactions
                if self.simulation.enabled or "interactions" in self.simulation.model_fields_set
                else ()
            ),
            yaw=self.camera.yaw,
            pitch=self.camera.pitch,
            zoom=self.camera.zoom,
            orbit_degrees=self.camera.orbit_degrees if self.camera.mode == "orbit" else 0,
        )
