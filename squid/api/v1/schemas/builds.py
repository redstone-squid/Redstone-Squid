"""Public build representations."""

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from squid.builds.application.edit_schema import BuildPatch as BuildPatch
from squid.builds.application.edit_schema import DoorPatch as DoorPatch
from squid.builds.application.edit_schema import ExtenderPatch as ExtenderPatch
from squid.builds.application.queries import PublicBuildDimensions, PublicBuildSummary
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild, ExtenderBuild


class BuildStatusFilter(StrEnum):
    """Moderation state selectable on the authoritative build collection."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"

    def to_domain(self) -> Status:
        return Status[self.name]


class BuildLinks(BaseModel):
    """Allowlisted public media links attached to a build."""

    model_config = ConfigDict(extra="forbid")

    images: list[str]
    videos: list[str]
    world_downloads: list[str]
    schematics: list[str]
    renders: list[str]


class BuildSponsor(BaseModel):
    """Public sponsor details captured when the build was finalized."""

    model_config = ConfigDict(extra="forbid")

    installation_id: UUID
    display_name: str | None
    address: str | None
    description: str | None
    website_url: AnyHttpUrl | None


class DoorDetails(BaseModel):
    """Measurements and patterns specific to doors.

    The build summary already includes opening_time and closing_time. This section
    adds the visible timings and opening dimensions without repeating those fields.
    """

    model_config = ConfigDict(extra="forbid")

    category: Literal["Door"] = "Door"
    door_dimensions: PublicBuildDimensions
    orientation: str | None
    patterns: list[str]
    visible_opening_time: int | None
    visible_closing_time: int | None


class ExtenderDetails(BaseModel):
    """Facts owned by piston extenders. Timings are in game ticks."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["Extender"] = "Extender"
    orientation: str | None
    patterns: list[str]
    extension_length: int | None
    extender_type: str | None
    extension_time: int | None
    retraction_time: int | None


class GeneralDetails(BaseModel):
    """The categories that add no facts beyond the shared ones."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["Utility", "Entrance", "Other"]


type BuildDetails = Annotated[DoorDetails | ExtenderDetails | GeneralDetails, Field(discriminator="category")]


def _details(build: Build) -> BuildDetails:
    """Project the category-specific facts, keyed by the build's own category."""
    match build:
        case DoorBuild():
            return DoorDetails(
                door_dimensions=PublicBuildDimensions(
                    width=build.door_width, height=build.door_height, depth=build.door_depth
                ),
                orientation=build.orientation,
                patterns=list(build.patterns),
                visible_opening_time=build.visible_opening_time,
                visible_closing_time=build.visible_closing_time,
            )
        case ExtenderBuild():
            return ExtenderDetails(
                orientation=build.orientation,
                patterns=list(build.patterns),
                extension_length=build.extension_length,
                extender_type=build.extender_type,
                extension_time=build.extension_time,
                retraction_time=build.retraction_time,
            )
        case _:
            return GeneralDetails(category=cast(Literal["Utility", "Entrance", "Other"], build.category.value))


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildDetail(PublicBuildSummary):
    """A build summary with category-specific details, restrictions, and media links.

    Read details.category to determine which fields are available. For example,
    a door has door_dimensions; utility builds have no category-specific fields.
    """

    details: BuildDetails
    restrictions: dict[str, list[str]]
    description: str | None
    links: BuildLinks
    sponsor: BuildSponsor | None = None

    @classmethod
    def from_domain(cls, build: Build, /) -> Self:
        """Render public detail without raw extra_info or account identifiers."""
        summary = PublicBuildSummary.from_build(build)
        return cls(
            **{field.name: getattr(summary, field.name) for field in fields(summary)},
            details=_details(build),
            restrictions={name: list(values or ()) for name, values in build.restrictions.items()},
            description=build.description,
            links=BuildLinks(
                images=list(build.image_urls),
                videos=list(build.video_urls),
                world_downloads=list(build.world_download_urls),
                schematics=list(build.schematic_urls),
                renders=list(build.render_urls),
            ),
            sponsor=(
                None
                if build.sponsor is None
                else BuildSponsor(
                    installation_id=build.sponsor.installation_id,
                    display_name=build.sponsor.display_name,
                    address=build.sponsor.address,
                    description=build.sponsor.description,
                    # PublicSponsor validates and normalizes this into an AnyHttpUrl string on
                    # construction, and pydantic validates it again here; only the synthesized
                    # __init__ signature insists on the parsed field type.
                    website_url=build.sponsor.website_url,  # pyright: ignore[reportArgumentType]
                )
            ),
        )
