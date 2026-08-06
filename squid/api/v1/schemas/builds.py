"""Public build representations."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from squid.builds.domain import Build, Status


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
