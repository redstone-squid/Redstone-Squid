"""Build drafts, shared behavior, and value types. Persisted entities live in squid.builds.models."""

import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import IntEnum, StrEnum
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NotRequired,
    Self,
    TypedDict,
    cast,
    get_args,
    overload,
)

from whenever import Instant

from squid.builds.errors import InvalidBuildError
from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.core.i18n import tr
from squid.messages.domain import MessageSnapshot
from squid.sponsors import PublicSponsor
from squid.tags.models import TagAssignment

RecordCategoryLiteral = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Sequence[RecordCategoryLiteral] = cast(
    Sequence[RecordCategoryLiteral], get_args(RecordCategoryLiteral)
)
BuildCategoryLiteral = Literal["Door", "Extender", "Utility", "Entrance", "Other"]
BUILD_TYPES: Sequence[BuildCategoryLiteral] = cast(Sequence[BuildCategoryLiteral], get_args(BuildCategoryLiteral))
DoorOrientationLiteral = Literal["Door", "Skydoor", "Trapdoor"]
DOOR_ORIENTATION_NAMES = cast(Sequence[DoorOrientationLiteral], get_args(DoorOrientationLiteral))
RestrictionTypeLiteral = Literal["wiring-placement", "animated", "component", "miscellaneous"]
RESTRICTIONS = cast(Sequence[RestrictionTypeLiteral], get_args(RestrictionTypeLiteral))
# `build_links.media_type` is plain text with no CHECK constraint, so widening this needs no
# schema change. `schematic` links point at the user-facing download of an uploaded file;
# `render` links point at a generated preview image and are always replaceable.
MediaTypeLiteral = Literal["image", "video", "world-download", "schematic", "render"]


class UnknownRestrictions(TypedDict, total=False):
    wiring_placement_restrictions: list[str]
    animated_restrictions: list[str]
    component_restrictions: list[str]
    miscellaneous_restrictions: list[str]


class ServerInfo(TypedDict, total=False):
    """Server address and directions for finding a build in the world."""

    server_ip: str
    coordinates: str
    command_to_build: str


class SchematicDuplicateSource(TypedDict):
    """The submitted attachment which produced duplicate evidence."""

    attachment_id: str
    filename: str


class SchematicDuplicateInfo(TypedDict):
    """One machine-detected schematic resemblance retained for reviewers."""

    build_id: int
    title: NotRequired[str]
    tier: Literal["identical", "structural-match", "near"]
    footprint_distance: float
    source_attachments: NotRequired[list[SchematicDuplicateSource]]


type AttachmentFailureStage = Literal["classification", "download", "mirror", "analysis", "duplicate-check", "record"]


class AttachmentFailureInfo(TypedDict):
    """One submitted attachment that could not complete enrichment."""

    attachment_id: str
    filename: str
    stage: AttachmentFailureStage
    detail: str


class Info(TypedDict, total=False):
    """Additional submission details retained in the build's JSON metadata."""

    unknown_patterns: list[str]
    unknown_restrictions: UnknownRestrictions
    server_info: ServerInfo
    schematic_duplicates: list[SchematicDuplicateInfo]
    attachment_failures: list[AttachmentFailureInfo]
    submission_provenance: dict[str, Any]


class Status(IntEnum):
    """The status of a submission."""

    PENDING = 0
    CONFIRMED = 1
    DENIED = 2


class BuildCategory(StrEnum):
    """The categories of the builds."""

    DOOR = "Door"
    EXTENDER = "Extender"
    UTILITY = "Utility"
    ENTRANCE = "Entrance"
    OTHER = "Other"


class FrozenField[T]:
    """A descriptor that makes an attribute immutable after it has been set."""

    __slots__ = ("_private_name",)

    def __init__(self, name: str) -> None:
        self._private_name = "__frozen_" + name

    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...

    @overload
    def __get__(self, instance: object, owner: type[object]) -> T: ...

    def __get__(self, instance: object | None, owner: type[object] | None = None) -> T | Self:
        if instance is None:
            return self
        return getattr(instance, self._private_name)

    def __set__(self, instance: object, value: T) -> None:
        if hasattr(instance, self._private_name):
            name = self._private_name[1:]
            raise InvalidStateError(tr(t"builds-attribute-immutable = Attribute `{name}` is immutable!")) from None

        setattr(instance, self._private_name, value)


def frozen_field(**kwargs: Any) -> Any:
    """A field that is immutable after it has been set. See `dataclasses.field` for more information."""
    metadata = kwargs.pop("metadata", {}) | {"frozen": True}
    return field(**kwargs, metadata=metadata)


def freeze_fields[T](cls: type[T]) -> type[T]:
    """Replace dataclass fields marked with frozen metadata by write-once descriptors.

    The presence of the metadata key is enough; its value is not checked.
    Already-frozen dataclasses are returned unchanged.

    Raises:
        InvalidStateError: If cls is not a dataclass.
    """

    cls_fields = getattr(cls, "__dataclass_fields__", None)
    if cls_fields is None:
        class_name = cls.__name__
        raise InvalidStateError(tr(t"builds-not-dataclass = {class_name} is not a dataclass"))

    params = cls.__dataclass_params__  # type: ignore
    if params.frozen:
        return cls

    for f in fields(cls):  # type: ignore
        if "frozen" in f.metadata:
            setattr(cls, f.name, FrozenField(f.name))
    return cls


class StagedMedia:
    """Link helpers shared by the entity and the draft.

    Expects the concrete dataclass to declare ``links``.
    """

    links: list[BuildLink]

    def urls_of(self, media_type: MediaTypeLiteral) -> tuple[str, ...]:
        """The URLs of one media type, in link order."""
        return tuple(link.url for link in self.links if link.media_type == media_type)

    def add_link(self, media_type: MediaTypeLiteral, url: str) -> None:
        """Attach one media URL, ignoring exact duplicates."""
        from squid.builds.models import BuildLink

        if not any(link.url == url and link.media_type == media_type for link in self.links):
            self.links.append(BuildLink(url=url, media_type=media_type))

    def replace_links(self, media_type: MediaTypeLiteral, urls: Iterable[str]) -> None:
        """Replace every link of one media type, preserving the others."""
        from squid.builds.models import BuildLink

        kept = [link for link in self.links if link.media_type != media_type]
        self.links[:] = [*kept, *(BuildLink(url=url, media_type=media_type) for url in urls)]

    @property
    def image_urls(self) -> tuple[str, ...]:
        return self.urls_of("image")

    @property
    def video_urls(self) -> tuple[str, ...]:
        return self.urls_of("video")

    @property
    def world_download_urls(self) -> tuple[str, ...]:
        return self.urls_of("world-download")

    @property
    def schematic_urls(self) -> tuple[str, ...]:
        return self.urls_of("schematic")

    @property
    def render_urls(self) -> tuple[str, ...]:
        return self.urls_of("render")


def sort_restrictions(
    restrictions: Sequence[str],
    definitions: Mapping[str, RestrictionTypeLiteral | None],
) -> dict[RestrictionTypeLiteral, list[str]]:
    """Group known names by restriction type using their canonical spelling.

    Matching ignores case. Unknown names are omitted; duplicates are retained
    in input order within each group.

    Raises:
        DataIntegrityError: If a known restriction has no recorded type.
    """
    definitions_by_name: dict[str, tuple[str, RestrictionTypeLiteral | None]] = {
        name.lower(): (name, restriction_type) for name, restriction_type in definitions.items()
    }
    buckets: dict[RestrictionTypeLiteral, list[str]] = {
        "wiring-placement": [],
        "animated": [],
        "component": [],
        "miscellaneous": [],
    }
    for restriction in restrictions:
        definition = definitions_by_name.get(restriction.lower())
        if definition is None:
            continue
        canonical_name, restriction_type = definition
        if restriction_type is None:
            msg = "The type is supposed to never be None, this is a bug in the database."
            raise DataIntegrityError(msg, context={"restriction": canonical_name})
        buckets[restriction_type].append(canonical_name)
    return buckets


class StagedTaxonomy:
    """Staged restriction helpers shared by the entity and the draft.

    Expects the concrete dataclass to declare the pattern and restriction lists.
    """

    patterns: list[str]
    wiring_placement_restrictions: list[str]
    animated_restrictions: list[str]
    component_restrictions: list[str]
    miscellaneous_restrictions: list[str]

    @property
    def restrictions(
        self,
    ) -> dict[
        Literal[
            "wiring_placement_restrictions",
            "animated_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
        ],
        Sequence[str] | None,
    ]:
        """The staged restriction names, keyed by bucket."""
        return {
            "wiring_placement_restrictions": self.wiring_placement_restrictions,
            "animated_restrictions": self.animated_restrictions,
            "component_restrictions": self.component_restrictions,
            "miscellaneous_restrictions": self.miscellaneous_restrictions,
        }

    def classify_restrictions(
        self,
        restrictions: Sequence[str],
        definitions: Mapping[str, RestrictionTypeLiteral | None],
    ) -> None:
        """Replace restrictions using already-loaded classification metadata."""
        sorted_restrictions = sort_restrictions(restrictions, definitions)
        self.wiring_placement_restrictions = sorted_restrictions["wiring-placement"]
        self.animated_restrictions = sorted_restrictions["animated"]
        self.component_restrictions = sorted_restrictions["component"]
        self.miscellaneous_restrictions = sorted_restrictions["miscellaneous"]


@dataclass(kw_only=True)
class BuildDraft(StagedMedia, StagedTaxonomy):
    """Build details collected before the category and required fields are known.

    This is an in-memory staging object used by inference and build creation.
    It differs from a submission draft (DraftSnapshot), which retains a user's
    form answers and revision in the submission workflow.

    Callers can fill fields progressively. finalize() creates an unsaved Build
    of the selected category and supplies defaults; submitting that build and
    validating submission rules happen in the application services.
    """

    category: BuildCategory | None = None
    submission_status: Status | None = None
    versions: list[str] = field(default_factory=list)
    version_spec: str | None = None

    width: int | None = None
    height: int | None = None
    depth: int | None = None

    patterns: list[str] = field(default_factory=list)
    wiring_placement_restrictions: list[str] = field(default_factory=list)
    animated_restrictions: list[str] = field(default_factory=list)
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)
    tags: list[TagAssignment] = field(default_factory=list)

    extra_info: Info = field(default_factory=Info)
    creators_ign: list[str] = field(default_factory=list)
    links: list[BuildLink] = field(default_factory=list)

    display_name: str | None = None
    source_submission_draft_id: uuid.UUID | None = None
    sponsor: PublicSponsor | None = None
    submitter_account_id: int | None = None
    completion_time: str | None = None
    completion_at: Instant | None = None
    completion_evidence: str | None = None
    description: str | None = None

    source_messages: tuple[MessageSnapshot, ...] = ()

    ai_generated: bool | None = None

    # Category-specific facts, staged flat until the category is known.
    door_orientation: DoorOrientationLiteral | None = None
    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None
    normal_opening_time: int | None = None
    normal_closing_time: int | None = None
    visible_opening_time: int | None = None
    visible_closing_time: int | None = None
    extender_orientation: str | None = None
    extension_length: int | None = None
    extender_type: str | None = None

    @property
    def dimensions(self) -> tuple[int | None, int | None, int | None]:
        return self.width, self.height, self.depth

    @dimensions.setter
    def dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.width, self.height, self.depth = dimensions

    @property
    def door_dimensions(self) -> tuple[int | None, int | None, int | None]:
        return self.door_width, self.door_height, self.door_depth

    @door_dimensions.setter
    def door_dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.door_width, self.door_height, self.door_depth = dimensions

    def finalize(self) -> Build:
        """Create an unsaved category entity and apply its defaults.

        Lists are copied, but their entries and extra_info can still be shared with
        the draft. Door width and height default to 1 and 2 when omitted.

        Raises:
            InvalidBuildError: If no category has been set.
        """
        from squid.builds.models import DoorBuild, EntranceBuild, ExtenderBuild, OtherBuild, UtilityBuild

        if self.category is None:
            msg = "A build draft cannot be finalized before its category is known."
            raise InvalidBuildError(msg, context={"category": None})
        common: dict[str, Any] = {
            "submission_status": self.submission_status,
            "versions": list(self.versions),
            "version_spec": self.version_spec,
            "width": self.width,
            "height": self.height,
            "depth": self.depth,
            "patterns": list(self.patterns),
            "wiring_placement_restrictions": list(self.wiring_placement_restrictions),
            "animated_restrictions": list(self.animated_restrictions),
            "component_restrictions": list(self.component_restrictions),
            "miscellaneous_restrictions": list(self.miscellaneous_restrictions),
            "tags": list(self.tags),
            "extra_info": self.extra_info,
            "creators_ign": list(self.creators_ign),
            "links": list(self.links),
            "display_name": self.display_name,
            "source_submission_draft_id": self.source_submission_draft_id,
            "sponsor": self.sponsor,
            "submitter_account_id": self.submitter_account_id,
            "completion_time": self.completion_time,
            "completion_at": self.completion_at,
            "completion_evidence": self.completion_evidence,
            "description": self.description,
            "source_messages": tuple(self.source_messages),
            "ai_generated": self.ai_generated,
        }
        match self.category:
            case BuildCategory.DOOR:
                return DoorBuild(
                    **common,
                    orientation=self.door_orientation or "Door",
                    door_width=self.door_width if self.door_width is not None else 1,
                    door_height=self.door_height if self.door_height is not None else 2,
                    door_depth=self.door_depth,
                    normal_opening_time=self.normal_opening_time,
                    normal_closing_time=self.normal_closing_time,
                    visible_opening_time=self.visible_opening_time,
                    visible_closing_time=self.visible_closing_time,
                )
            case BuildCategory.EXTENDER:
                return ExtenderBuild(
                    **common,
                    orientation=self.extender_orientation,
                    extension_length=self.extension_length,
                    extender_type=self.extender_type,
                )
            case BuildCategory.UTILITY:
                return UtilityBuild(**common)
            case BuildCategory.ENTRANCE:
                return EntranceBuild(**common)
            case BuildCategory.OTHER:
                return OtherBuild(**common)


_TIME_PATTERN = re.compile(r"[~≈]?\s*(?P<value>[+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?P<unit>[a-z][a-z\s_-]*)?")
"""A number with an optional approximation marker and an optional unit."""

_TICKS_PER_UNIT: Mapping[str, float] = {
    # A bare number is seconds, which is how submissions and the inference prompt quote timings.
    "": 20,
    "s": 20,
    "sec": 20,
    "secs": 20,
    "second": 20,
    "seconds": 20,
    "t": 1,
    "gt": 1,
    "tick": 1,
    "ticks": 1,
    "gametick": 1,
    "gameticks": 1,
    "rt": 2,
    "redstonetick": 2,
    "redstoneticks": 2,
}


def parse_time_string(time_string: str | None) -> int | None:
    """Convert a time string to game ticks, or return None for unrecognized input.

    A bare number means seconds; one second is 20 game ticks and one redstone
    tick is two game ticks. For example, "1.5s" gives 30 and "3 redstone ticks"
    gives 6. Approximation markers such as "~" are ignored. Fractional ticks
    use Python's round(), which rounds exact halves to the nearest even integer.
    """
    if time_string is None:
        return None

    match = _TIME_PATTERN.fullmatch(time_string.strip().lower())
    if match is None:
        return None

    # Separators inside a unit are noise, so "game ticks", "game-ticks" and "gameticks" all collapse to one key.
    unit = re.sub(r"[\s_-]+", "", match["unit"] or "")
    ticks_per_unit = _TICKS_PER_UNIT.get(unit)
    if ticks_per_unit is None:
        return None
    return round(float(match["value"]) * ticks_per_unit)


if TYPE_CHECKING:
    from squid.builds.models import Build, BuildLink
