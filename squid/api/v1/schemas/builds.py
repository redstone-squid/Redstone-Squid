"""Public build representations."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self, cast, override
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from squid.api.v1.schemas import FromDomain
from squid.builds.domain import Build, DoorBuild, ExtenderBuild, Status

type InputDimensions = tuple[int | None, int | None, int | None]

_HTTP_URL = TypeAdapter(AnyHttpUrl)


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


class DoorPatch(BaseModel):
    """A partial edit of the facts only a door has.

    Sending this for a build of another category is rejected: the category is
    structural, so there is no field to set.
    """

    model_config = ConfigDict(extra="forbid")

    door_dimensions: InputDimensions | None = None
    orientation: str | None = Field(default=None, max_length=100)
    patterns: list[str] | None = Field(default=None, max_length=50)
    opening_time: int | None = Field(default=None, ge=0)
    closing_time: int | None = Field(default=None, ge=0)

    # Wire name -> BuildEditPatch field name.
    _EDIT_FIELDS: ClassVar[Mapping[str, str]] = {
        "door_dimensions": "door_dimensions",
        "orientation": "door_orientation_type",
        "patterns": "door_type",
        "opening_time": "normal_opening_time",
        "closing_time": "normal_closing_time",
    }

    @model_validator(mode="after")
    def _reject_invalid_clears(self) -> Self:
        non_nullable = {"door_dimensions", "patterns"}
        invalid = sorted(name for name in self.model_fields_set & non_nullable if getattr(self, name) is None)
        if invalid:
            msg = f"fields cannot be null: {', '.join(invalid)}"
            raise ValueError(msg)
        if self.door_dimensions is not None and any(value is not None and value <= 0 for value in self.door_dimensions):
            msg = "door_dimensions must be positive when supplied"
            raise ValueError(msg)
        return self

    def edit_attributes(self) -> dict[str, object]:
        """Flatten the supplied fields onto the application patch's names."""
        supplied = self.model_dump(exclude_unset=True)
        return {self._EDIT_FIELDS[name]: value for name, value in supplied.items()}


class BuildPatch(BaseModel):
    """A partial build edit which preserves omitted versus explicitly cleared fields."""

    model_config = ConfigDict(extra="forbid")

    version_spec: str | None = Field(default=None, max_length=500)
    dimensions: InputDimensions | None = None
    door: DoorPatch | None = None
    wiring_placement_restrictions: list[str] | None = Field(default=None, max_length=100)
    animated_restrictions: list[str] | None = Field(default=None, max_length=100)
    component_restrictions: list[str] | None = Field(default=None, max_length=100)
    miscellaneous_restrictions: list[str] | None = Field(default=None, max_length=100)
    locationality: str | None = Field(default=None, max_length=100)
    directionality: str | None = Field(default=None, max_length=100)
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
            "door",
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
        if self.dimensions is not None and any(value is not None and value <= 0 for value in self.dimensions):
            msg = "dimensions must be positive when supplied"
            raise ValueError(msg)
        return self

    def edit_attributes(self) -> dict[str, object]:
        """Flatten the supplied fields onto the application patch's names.

        ``door: null`` is rejected above, so a supplied ``door`` is always an
        object whose own set fields are the ones to apply.
        """
        attributes: dict[str, object] = self.model_dump(exclude_unset=True)
        attributes.pop("door", None)
        if self.door is not None:
            attributes.update(self.door.edit_attributes())
        return attributes


class Dimensions(BaseModel):
    """A three-dimensional build measurement."""

    model_config = ConfigDict(extra="forbid")

    width: int | None
    height: int | None
    depth: int | None


class BuildTag(BaseModel):
    """A public tag assignment without moderation provenance."""

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
    tags: list[BuildTag]
    preview: BuildPreview | None
    version_spec: str | None
    versions: list[str]
    opening_time: int | None
    closing_time: int | None
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
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
            status=_status_name(build.submission_status),
            category=build.category.value if build.category is not None else "unknown",
            dimensions=Dimensions(width=build.width, height=build.height, depth=build.depth),
            creators=list(build.creators_ign),
            tags=[
                BuildTag(
                    key=assignment.definition.stable_key,
                    name=assignment.definition.display_name,
                    value=assignment.value,
                    unit=assignment.display_unit,
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


class BuildLinks(BaseModel):
    """Allowlisted public media links attached to a build."""

    model_config = ConfigDict(extra="forbid")

    images: list[str]
    videos: list[str]
    world_downloads: list[str]
    schematics: list[str]
    renders: list[str]


class BuildSponsor(BaseModel):
    """Immutable public sponsor metadata captured when the build was finalized."""

    model_config = ConfigDict(extra="forbid")

    installation_id: UUID
    display_name: str | None
    address: str | None
    description: str | None
    website_url: AnyHttpUrl | None


class DoorDetails(BaseModel):
    """Facts owned by doors.

    The headline ``opening_time`` and ``closing_time`` stay on the summary: the
    card projection and the search grammar both already name them there, so
    repeating them here would give one value two addresses in one payload.
    """

    model_config = ConfigDict(extra="forbid")

    category: Literal["Door"] = "Door"
    door_dimensions: Dimensions
    orientation: str
    patterns: list[str]
    visible_opening_time: int | None
    visible_closing_time: int | None


class ExtenderDetails(BaseModel):
    """Facts owned by piston extenders."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["Extender"] = "Extender"
    orientation: str | None
    patterns: list[str]
    extension_length: int | None
    extender_type: str | None


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
            )
        case _:
            return GeneralDetails(category=cast(Literal["Utility", "Entrance", "Other"], build.category.value))


class BuildDetail(BuildSummary):
    """Stable item representation with build-specific facts.

    Category-specific facts live under ``details``, a union discriminated by
    ``category``, so a client reads a door's opening size from a door and never
    from a nullable field on a utility.
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
                    website_url=build.sponsor.website_url,
                )
            ),
        )


def _preview(build: Build) -> BuildPreview | None:
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


def _status_name(status: Status | None) -> str:
    return status.name.casefold() if status is not None else "unknown"
