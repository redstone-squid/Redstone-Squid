"""Public build representations."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from squid.builds.domain import Build, Status

type InputDimensions = tuple[int | None, int | None, int | None]


class BuildStatusFilter(StrEnum):
    """Moderation state selectable on the authoritative build collection."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"

    def to_domain(self) -> Status:
        return Status[self.name]


class DoorSubmission(BaseModel):
    """A user-authored door build submission."""

    model_config = ConfigDict(extra="forbid")

    category: str = "door"
    door_size: InputDimensions
    pattern: list[str] = Field(default_factory=lambda: ["Regular"], max_length=50)
    door_type: Literal["Door", "Skydoor", "Trapdoor"] = "Door"
    build_size: InputDimensions = (None, None, None)
    works_in: str | None = Field(default=None, max_length=500)
    restrictions: list[str] = Field(default_factory=list, max_length=100)
    information_about_build: str | None = Field(default=None, max_length=10_000)
    normal_closing_time: int | None = Field(default=None, ge=0)
    normal_opening_time: int | None = Field(default=None, ge=0)
    date_of_creation: str | None = Field(default=None, max_length=100)
    creators: list[str] = Field(default_factory=list, max_length=100)
    locationality: Literal["Locational", "Locational with fixes", "Not locational"] | None = None
    directionality: Literal["Directional", "Directional with fixes", "Not directional"] | None = None
    image_urls: list[str] = Field(default_factory=list, max_length=100)
    video_urls: list[str] = Field(default_factory=list, max_length=100)
    world_download_urls: list[str] = Field(default_factory=list, max_length=100)
    schematic_urls: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _positive_dimensions(self) -> Self:
        for dimensions in (self.door_size, self.build_size):
            if any(value is not None and value <= 0 for value in dimensions):
                msg = "dimensions must be positive when supplied"
                raise ValueError(msg)
        return self


class BuildPatch(BaseModel):
    """A partial build edit which preserves omitted versus explicitly cleared fields."""

    model_config = ConfigDict(extra="forbid")

    version_spec: str | None = Field(default=None, max_length=500)
    dimensions: InputDimensions | None = None
    door_dimensions: InputDimensions | None = None
    door_type: list[str] | None = Field(default=None, max_length=50)
    door_orientation_type: str | None = Field(default=None, max_length=100)
    wiring_placement_restrictions: list[str] | None = Field(default=None, max_length=100)
    animated_restrictions: list[str] | None = Field(default=None, max_length=100)
    component_restrictions: list[str] | None = Field(default=None, max_length=100)
    miscellaneous_restrictions: list[str] | None = Field(default=None, max_length=100)
    locationality: str | None = Field(default=None, max_length=100)
    directionality: str | None = Field(default=None, max_length=100)
    normal_closing_time: int | None = Field(default=None, ge=0)
    normal_opening_time: int | None = Field(default=None, ge=0)
    extra_user_info: str | None = Field(default=None, max_length=10_000)
    creators_ign: list[str] | None = Field(default=None, max_length=100)
    image_urls: list[str] | None = Field(default=None, max_length=100)
    video_urls: list[str] | None = Field(default=None, max_length=100)
    world_download_urls: list[str] | None = Field(default=None, max_length=100)
    schematic_urls: list[str] | None = Field(default=None, max_length=100)
    render_urls: list[str] | None = Field(default=None, max_length=100)
    server_ip: str | None = Field(default=None, max_length=500)
    coordinates: str | None = Field(default=None, max_length=500)
    command_to_get_to_build: str | None = Field(default=None, max_length=2_000)
    completion_time: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _reject_invalid_clears(self) -> Self:
        non_nullable = {
            "dimensions",
            "door_dimensions",
            "door_type",
            "wiring_placement_restrictions",
            "animated_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
            "locationality",
            "directionality",
            "creators_ign",
            "image_urls",
            "video_urls",
            "world_download_urls",
            "schematic_urls",
            "render_urls",
        }
        invalid = sorted(name for name in self.model_fields_set & non_nullable if getattr(self, name) is None)
        if invalid:
            msg = f"fields cannot be null: {', '.join(invalid)}"
            raise ValueError(msg)
        for name in self.model_fields_set & {"dimensions", "door_dimensions"}:
            dimensions = getattr(self, name)
            if dimensions is not None and any(value is not None and value <= 0 for value in dimensions):
                msg = f"{name} must be positive when supplied"
                raise ValueError(msg)
        return self


class Dimensions(BaseModel):
    """A three-dimensional build measurement."""

    model_config = ConfigDict(extra="forbid")

    width: int | None
    height: int | None
    depth: int | None


class BuildTag(BaseModel):
    """A public tag assignment without moderation provenance."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: Decimal | str | bool | None
    unit: str | None


class BuildSummary(BaseModel):
    """Stable collection representation of a build."""

    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    status: str
    category: str
    dimensions: Dimensions
    creators: list[str]
    tags: list[BuildTag]
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, build: Build) -> "BuildSummary":
        """Render allowlisted public build fields."""
        if build.id is None:
            msg = "persisted build is missing its identifier"
            raise ValueError(msg)
        return cls(
            id=build.id,
            title=build.title,
            status=_status_name(build.submission_status),
            category=build.category.value if build.category is not None else "unknown",
            dimensions=Dimensions(width=build.width, height=build.height, depth=build.depth),
            creators=list(build.creators_ign),
            tags=[
                BuildTag(
                    name=assignment.definition.display_name,
                    value=assignment.value,
                    unit=assignment.display_unit,
                )
                for assignment in build.tags
            ],
            created_at=build.submission_time.to_stdlib() if build.submission_time is not None else None,
            updated_at=build.edited_time.to_stdlib() if build.edited_time is not None else None,
        )


class BuildLinks(BaseModel):
    """Allowlisted public media links attached to a build."""

    model_config = ConfigDict(extra="forbid")

    images: list[str]
    videos: list[str]
    world_downloads: list[str]
    schematics: list[str]
    renders: list[str]


class BuildDetail(BuildSummary):
    """Stable item representation with build-specific facts."""

    version_spec: str | None
    versions: list[str]
    door_dimensions: Dimensions
    patterns: list[str]
    orientation: str | None
    restrictions: dict[str, list[str]]
    opening_time: int | None
    closing_time: int | None
    description: str | None
    links: BuildLinks

    @classmethod
    @override
    def from_domain(cls, build: Build) -> "BuildDetail":
        """Render public detail without raw extra_info or account identifiers."""
        summary = BuildSummary.from_domain(build)
        return cls(
            **summary.model_dump(),
            version_spec=build.version_spec,
            versions=list(build.versions),
            door_dimensions=Dimensions(width=build.door_width, height=build.door_height, depth=build.door_depth),
            patterns=list(build.door_type),
            orientation=build.door_orientation_type,
            restrictions={name: list(values or ()) for name, values in build.restrictions.items()},
            opening_time=build.normal_opening_time,
            closing_time=build.normal_closing_time,
            description=build.description,
            links=BuildLinks(
                images=list(build.image_urls),
                videos=list(build.video_urls),
                world_downloads=list(build.world_download_urls),
                schematics=list(build.schematic_urls),
                renders=list(build.render_urls),
            ),
        )


def _status_name(status: Status | None) -> str:
    return status.name.casefold() if status is not None else "unknown"
