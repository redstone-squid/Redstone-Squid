"""Public build representations."""

from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from squid.api.v1.schemas.schematics import SchematicSummary
from squid.core.auth.application.viewer import ViewerBallot
from squid.core.builds.application.edit_schema import BuildPatch as BuildPatch
from squid.core.builds.application.edit_schema import DoorPatch as DoorPatch
from squid.core.builds.application.edit_schema import ExtenderPatch as ExtenderPatch
from squid.core.builds.application.queries import PublicBuildDimensions, PublicBuildSummary
from squid.core.builds.domain import BuildStatus
from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.builds.history import BuildLastEdit, HistoryActor
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.records.domain.build_records import BuildRecord


class BuildStatusFilter(StrEnum):
    """Moderation state selectable on the authoritative build collection."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    def to_domain(self) -> BuildStatus:
        return BuildStatus[self.name]


class BuildLinks(BaseModel):
    """Allowlisted public media links attached to a build."""

    model_config = ConfigDict(extra="forbid")

    world_downloads: list[str]
    schematics: list[str]


class BuildSchematicOverview(BaseModel):
    count: int
    primary: SchematicSummary | None


class BuildMedia(BaseModel):
    kind: Literal["image", "video", "render"]
    url: str
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    alt: str | None = None


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
    expansion: DoorExpansion | None = None
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
                expansion=build.expansion,
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


@dataclass(frozen=True, slots=True)
class BuildViewer:
    can_edit: bool
    can_review: bool
    can_download_private_schematics: bool
    followed_account_subscription_ids: dict[UUID, int]
    ballot: ViewerBallot | None


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
    media: list[BuildMedia]
    approved_at: datetime | None = None
    submitted_by: HistoryActor | None = None
    last_edited: BuildLastEdit | None = None
    history_count: int = 0
    vote_session_id: int | None = None
    records_held: tuple[BuildRecord, ...] = ()
    records_former: tuple[BuildRecord, ...] = ()
    submitted_at: datetime | None
    schematics: BuildSchematicOverview | None = None
    viewer: BuildViewer | None = None
    sponsor: BuildSponsor | None = None

    @classmethod
    def from_domain(cls, build: Build, /) -> Self:
        """Render public detail without raw extra_info or account identifiers."""
        summary = PublicBuildSummary.from_build(build)
        return cls(
            **{field.name: getattr(summary, field.name) for field in fields(summary)},
            details=_details(build),
            submitted_at=summary.created_at,
            records_held=build.published_records.held,
            records_former=build.published_records.former,
            restrictions={name: list(values or ()) for name, values in build.restrictions.items()},
            description=build.description,
            media=[
                *(BuildMedia(kind="render", url=url) for url in build.render_urls),
                *(BuildMedia(kind="image", url=url) for url in build.image_urls),
                *(BuildMedia(kind="video", url=url) for url in build.video_urls),
            ],
            links=BuildLinks(
                world_downloads=list(build.world_download_urls),
                schematics=list(build.schematic_urls),
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
