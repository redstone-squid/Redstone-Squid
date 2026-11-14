"""Public build representations."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self, cast, override
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from squid.api.v1.schemas import FromDomain
from squid.builds.application.edit_schema import BuildPatch as BuildPatch
from squid.builds.application.edit_schema import DoorPatch as DoorPatch
from squid.builds.application.edit_schema import ExtenderPatch as ExtenderPatch
from squid.builds.application.queries import PublicBuildSummary
from squid.builds.domain import Status
from squid.builds.domain.credits import Credit, CreditAttribution, CreditKind, CreditRole
from squid.builds.models import Build, DoorBuild, ExtenderBuild

_HTTP_URL = TypeAdapter(AnyHttpUrl)


class BuildStatusFilter(StrEnum):
    """Moderation state selectable on the authoritative build collection."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"

    def to_domain(self) -> Status:
        return Status[self.name]


class Dimensions(BaseModel):
    """A three-dimensional build measurement."""

    model_config = ConfigDict(extra="forbid")

    width: int | None
    height: int | None
    depth: int | None


class BuildTag(BaseModel):
    """A tag on a build, without who applied it or how."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    value: Decimal | str | bool | None
    unit: str | None


class BuildPreview(BaseModel):
    """The preferred HTTPS image for a build card."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["render", "image"]
    url: str


class BuildCreditSummary(BaseModel):
    """Public attribution without private account links or provider identifiers."""

    name: str
    kind: CreditKind
    username: str | None
    role: CreditRole
    attribution: CreditAttribution

    @classmethod
    def from_credit(cls, credit: Credit) -> BuildCreditSummary:
        return cls(
            name=credit.name,
            kind=credit.kind,
            username=credit.username,
            role=credit.role,
            attribution=credit.attribution,
        )


class BuildSummary(FromDomain[Build]):
    """Stable collection representation of a build."""

    model_config = ConfigDict(extra="forbid")

    id: int
    revision: int
    title: str
    display_name: str | None
    status: str
    category: str
    dimensions: Dimensions
    creators: list[str]
    credits: list[BuildCreditSummary] = Field(default_factory=list)
    tags: list[BuildTag]
    preview: BuildPreview | None
    version_spec: str | None
    versions: list[str]
    opening_time: int | None
    closing_time: int | None
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    @override
    def from_domain(cls, build: Build, /) -> Self:
        """Render allowlisted public build fields."""
        if build.id is None:
            msg = "persisted build is missing its identifier"
            raise ValueError(msg)
        return cls(
            id=build.id,
            revision=build.revision,
            title=build.title,
            display_name=build.display_name,
            status=build.submission_status.name.casefold() if build.submission_status is not None else "unknown",
            category=build.category.value,
            dimensions=Dimensions(width=build.width, height=build.height, depth=build.depth),
            creators=list(build.creators_ign),
            credits=[BuildCreditSummary.from_credit(credit) for credit in build.credits],
            tags=[
                BuildTag(
                    key=assignment.definition.stable_key,
                    name=assignment.definition.display_name,
                    value=assignment.presentation_value,
                    unit=assignment.presentation_unit.key if assignment.presentation_unit else None,
                )
                for assignment in build.tags
            ],
            preview=_preview(build),
            version_spec=build.version_spec,
            versions=list(build.versions),
            opening_time=build.normal_opening_time if isinstance(build, DoorBuild) else None,
            closing_time=build.normal_closing_time if isinstance(build, DoorBuild) else None,
            created_at=build.submission_time.to_stdlib() if build.submission_time is not None else None,
            updated_at=build.edited_time.to_stdlib() if build.edited_time is not None else None,
        )

    @classmethod
    def from_public_summary(cls, build: PublicBuildSummary, /) -> Self:
        """Serialize a build-owned public summary without loading an aggregate."""
        return cls(
            id=build.id,
            revision=build.revision,
            title=build.title,
            display_name=build.display_name,
            status=build.status,
            category=build.category,
            dimensions=Dimensions(width=build.dimensions[0], height=build.dimensions[1], depth=build.dimensions[2]),
            creators=list(build.creators),
            credits=[BuildCreditSummary.from_credit(credit) for credit in build.credits],
            tags=[BuildTag(key=tag.key, name=tag.name, value=tag.value, unit=tag.unit) for tag in build.tags],
            preview=(
                BuildPreview(kind=build.preview.kind, url=build.preview.url) if build.preview is not None else None
            ),
            version_spec=build.version_spec,
            versions=list(build.versions),
            opening_time=build.opening_time,
            closing_time=build.closing_time,
            created_at=build.created_at,
            updated_at=build.updated_at,
        )


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

    BuildSummary already includes opening_time and closing_time. This section
    adds the visible timings and opening dimensions without repeating those fields.
    """

    model_config = ConfigDict(extra="forbid")

    category: Literal["Door"] = "Door"
    door_dimensions: Dimensions
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
                door_dimensions=Dimensions(width=build.door_width, height=build.door_height, depth=build.door_depth),
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


class BuildDetail(BuildSummary):
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
    @override
    def from_domain(cls, build: Build, /) -> Self:
        """Render public detail without raw extra_info or account identifiers."""
        summary = BuildSummary.from_domain(build)
        return cls(
            **summary.model_dump(),
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


def _preview(build: Build) -> BuildPreview | None:
    """Return the first valid HTTPS render URL, falling back to image URLs."""
    sources: tuple[tuple[Literal["render", "image"], tuple[str, ...]], ...] = (
        ("render", build.render_urls),
        ("image", build.image_urls),
    )
    for kind, urls in sources:
        for candidate in urls:
            try:
                url = _HTTP_URL.validate_python(candidate)
            except ValidationError:
                continue
            if url.scheme == "https":
                return BuildPreview(kind=kind, url=str(url))
    return None
