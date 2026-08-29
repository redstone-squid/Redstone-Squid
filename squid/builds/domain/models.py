"""Build domain entities and value objects.

The persisted aggregate is a closed hierarchy: :class:`Build` carries the facts
shared by every category and one subclass per :class:`BuildCategory` carries the
category-specific ones, mirroring the joined-table persistence model. A build's
category is a fact of its type and can never change after construction.

Pre-submission flows that accumulate facts before the category is known (the
guided Discord form, message inference) use :class:`BuildDraft`, which is flat
and fully optional, and produce an entity with :meth:`BuildDraft.finalize`.
"""

import re
import typing
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import IntEnum, StrEnum
from typing import (
    Any,
    ClassVar,
    Final,
    Literal,
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
from squid.sponsors import PublicSponsor
from squid.tags.domain import TagAssignment

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
    """Various additional information about the server"""

    server_ip: str
    coordinates: str
    command_to_build: str


class SchematicDuplicateInfo(TypedDict):
    """One machine-detected schematic resemblance retained for reviewers."""

    build_id: int
    tier: Literal["identical", "structural-match", "near"]
    footprint_distance: float


class Info(TypedDict, total=False):
    """A special JSON field in the database that stores various additional information about the build"""

    user: str  # Provided by the submitter if they have any additional information to provide.
    unknown_patterns: list[str]
    unknown_restrictions: UnknownRestrictions
    server_info: ServerInfo
    # Set when an attached schematic measures differently from the declared dimensions. The
    # declared value still wins — an export is often cropped to the mechanism — so this records
    # the disagreement for reviewers rather than resolving it.
    schematic_dimension_mismatch: str
    schematic_duplicates: list[SchematicDuplicateInfo]
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
            raise InvalidStateError(tr(t"Attribute `{name}` is immutable!")) from None

        setattr(instance, self._private_name, value)


def frozen_field(**kwargs: Any) -> Any:
    """A field that is immutable after it has been set. See `dataclasses.field` for more information."""
    metadata = kwargs.pop("metadata", {}) | {"frozen": True}
    return field(**kwargs, metadata=metadata)


def freeze_fields[T](cls: type[T]) -> type[T]:
    """
    A decorator that makes fields of a dataclass immutable, if they have the `frozen` metadata set to True.

    This is done by replacing the fields with FrozenField descriptors.

    Args:
        cls: The class to make immutable, must be a dataclass.

    Raises:
        TypeError: If cls is not a dataclass
    """

    cls_fields = getattr(cls, "__dataclass_fields__", None)
    if cls_fields is None:
        class_name = cls.__name__
        raise InvalidStateError(tr(t"{class_name} is not a dataclass"))

    params = cls.__dataclass_params__  # type: ignore
    if params.frozen:
        return cls

    for f in fields(cls):  # type: ignore
        if "frozen" in f.metadata:
            setattr(cls, f.name, FrozenField(f.name))
    return cls


@dataclass(frozen=True, slots=True)
class BuildLink:
    """One media URL attached to a build.

    The database keys links by ``(build_id, url)``, so a URL carries exactly one
    media type per build; modelling links as one typed collection makes the
    conflicting state unrepresentable.
    """

    url: str
    media_type: MediaTypeLiteral


@dataclass(frozen=True, slots=True)
class SourceMessage:
    """A Discord message a build was submitted or inferred from.

    A build can have several: a submission is often a body message plus follow-up
    images, and one build-log message can yield several builds at once.
    """

    message_id: int
    guild_id: int | None = None
    channel_id: int | None = None
    author_id: int | None = None
    content: str | None = None

    @property
    def link(self) -> str | None:
        """The Discord jump link to the message."""
        if self.channel_id is None:
            return None
        if self.guild_id is None:
            msg = "This message is from DMs."
            raise NotImplementedError(msg)
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"


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
        candidate = BuildLink(url=url, media_type=media_type)
        if candidate not in self.links:
            self.links.append(candidate)

    def replace_links(self, media_type: MediaTypeLiteral, urls: Iterable[str]) -> None:
        """Replace every link of one media type, preserving the others."""
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
    """Group restriction names into their buckets, canonicalizing the spelling.

    A free function rather than a method, because a caller staging an edit needs the buckets
    without a build to write them onto: `/build edit` takes restrictions as one option and has
    to distribute them across the workspace's per-bucket fields before anything is applied.

    Raises:
        DataIntegrityError: If a known restriction has no type recorded.
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


@freeze_fields
@dataclass(kw_only=True)
class Build(StagedMedia, StagedTaxonomy):
    """The facts shared by every build category.

    Do not instantiate this class directly: every persisted build belongs to
    exactly one category subclass (:class:`DoorBuild`, :class:`ExtenderBuild`,
    :class:`UtilityBuild`, :class:`EntranceBuild`, :class:`OtherBuild`), and
    ``category`` is derived from the type. Use :class:`BuildDraft` while the
    category is still unknown.

    The four restriction lists and ``patterns`` are *staged taxonomy input*:
    callers write requested display names into them, and
    `squid.builds.application.taxonomy.apply_build_taxonomy` canonicalizes them
    into ``tags`` — the persisted source of truth — before any save.
    """

    category: ClassVar[BuildCategory]

    id: int | None = None
    revision: int = 1
    submission_status: Status | None = None
    record_category: RecordCategoryLiteral | None = None
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
    sponsor: Final[PublicSponsor | None] = frozen_field(default=None)
    submitter_account_id: int | None = None
    submitter_discord_id: int | None = None
    """Read-only derived state, filled on load for Discord rendering.

    Ownership is `submitter_account_id` and nothing reads this to decide anything.
    Named for the provider so it stops sitting ambiguously beside the account id -- that
    ambiguity is what let the edit ownership test compare a snowflake to a snowflake
    while a perfectly good account id sat one attribute away."""
    completion_time: str | None = None
    completion_at: Instant | None = None
    completion_evidence: str | None = None
    description: str | None = None
    submission_time: Instant | None = None
    edited_time: Instant | None = None

    source_messages: Final[tuple[SourceMessage, ...]] = frozen_field(default=())

    ai_generated: bool | None = None
    embedding: list[float] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self) is Build:
            raise InvalidStateError(
                tr(t"Build cannot be instantiated directly; construct a category subclass or a BuildDraft.")
            )

    @property
    def original_link(self) -> str | None:
        """The jump link to the message this build was submitted from, if any.

        The first source message is the submission itself; later ones are follow-ups
        such as images, so linking anything else would point away from the request.
        """
        for message in self.source_messages:
            if (link := message.link) is not None:
                return link
        return None

    @property
    def dimensions(self) -> tuple[int | None, int | None, int | None]:
        """The dimensions of the build."""
        return self.width, self.height, self.depth

    @dimensions.setter
    def dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.width, self.height, self.depth = dimensions

    @property
    def title(self) -> str:
        """The user-facing title, including individual-build UX decoration."""
        from squid.builds.domain.titles import format_build_display_title

        return format_build_display_title(self, markdown=True)

    def diff[T: Any](self, other: Build, *, allow_different_id: bool = False) -> list[tuple[str, T, T]]:
        """
        Returns the differences between this build and another of the same category.

        Values are rendered as plain data — callers persist the result as JSON —
        so link collections come back as one entry per media type rather than as
        :class:`BuildLink` objects.

        Args:
            other: Another build to compare to.
            allow_different_id: Whether the ID of the builds can be different.

        Returns:
            A list of tuples containing the attribute name, the value of this build, and the value of the other build.

        Raises:
            InvalidBuildError: If the IDs differ and allow_different_id is False, or the categories differ.
        """
        if type(self) is not type(other):
            msg = "Cannot diff builds of different categories."
            raise InvalidBuildError(msg, context={"left": type(self).__name__, "right": type(other).__name__})
        if self.id != other.id and not allow_different_id:
            msg = "The IDs of the builds are different."
            raise InvalidBuildError(msg, context={"left_id": self.id, "right_id": other.id})

        differences: list[tuple[str, T, T]] = []
        for f in fields(self):
            if f.name in {"id", "links"} or not f.compare:
                continue
            if getattr(self, f.name) != getattr(other, f.name):
                differences.append((f.name, getattr(self, f.name), getattr(other, f.name)))
        for media_type in get_args(MediaTypeLiteral):
            mine = list(self.urls_of(media_type))
            theirs = list(other.urls_of(media_type))
            if mine != theirs:
                differences.append((f"{media_type.replace('-', '_')}_urls", cast(T, mine), cast(T, theirs)))
        return differences

    def get_attr_type(self, attribute: str) -> type:
        """Gets the declared type of a field or property on this build's class."""
        cls = type(self)
        if attribute in typing.get_type_hints(cls):
            return typing.get_type_hints(cls)[attribute]
        cls_attr = getattr(cls, attribute, None)
        if isinstance(cls_attr, property) and cls_attr.fget is not None:
            return typing.get_type_hints(cls_attr.fget)["return"]
        msg = f"Attribute {attribute} is not on {cls.__name__}."
        raise InvalidBuildError(msg, context={"attribute": attribute})


@dataclass(kw_only=True)
class DoorBuild(Build):
    """A door: a mechanism that opens and closes an opening in a wall."""

    category: ClassVar[BuildCategory] = BuildCategory.DOOR

    orientation: DoorOrientationLiteral = "Door"
    door_width: int = 1
    door_height: int = 2
    door_depth: int | None = None

    normal_opening_time: int | None = None
    normal_closing_time: int | None = None
    visible_opening_time: int | None = None
    visible_closing_time: int | None = None

    @property
    def door_dimensions(self) -> tuple[int, int, int | None]:
        """The dimensions of the door (hallway)."""
        return self.door_width, self.door_height, self.door_depth


@dataclass(kw_only=True)
class ExtenderBuild(Build):
    """A piston extender."""

    category: ClassVar[BuildCategory] = BuildCategory.EXTENDER

    orientation: str | None = None
    extension_length: int | None = None
    extender_type: str | None = None


@dataclass(kw_only=True)
class UtilityBuild(Build):
    """A redstone utility."""

    category: ClassVar[BuildCategory] = BuildCategory.UTILITY


@dataclass(kw_only=True)
class EntranceBuild(Build):
    """An entrance that is not a door."""

    category: ClassVar[BuildCategory] = BuildCategory.ENTRANCE


@dataclass(kw_only=True)
class OtherBuild(Build):
    """A build outside the named categories."""

    category: ClassVar[BuildCategory] = BuildCategory.OTHER


BUILD_CLASS_BY_CATEGORY: Mapping[BuildCategory, type[Build]] = {
    BuildCategory.DOOR: DoorBuild,
    BuildCategory.EXTENDER: ExtenderBuild,
    BuildCategory.UTILITY: UtilityBuild,
    BuildCategory.ENTRANCE: EntranceBuild,
    BuildCategory.OTHER: OtherBuild,
}


@dataclass(kw_only=True)
class BuildDraft(StagedMedia, StagedTaxonomy):
    """A mutable pre-category accumulator for guided submission and inference.

    Every field is optional so transports can fill it progressively; nothing is
    validated until :meth:`finalize` produces the category subclass.
    """

    category: BuildCategory | None = None
    submission_status: Status | None = None
    record_category: RecordCategoryLiteral | None = None
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

    source_messages: tuple[SourceMessage, ...] = ()

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
        """Produce the category entity, applying category defaults explicitly.

        Raises:
            InvalidBuildError: If no category has been set.
        """
        if self.category is None:
            msg = "A build draft cannot be finalized before its category is known."
            raise InvalidBuildError(msg, context={"category": None})
        common: dict[str, Any] = {
            "submission_status": self.submission_status,
            "record_category": self.record_category,
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
    """Parses a time string into an integer.

    Args:
        time_string: The time string to parse, such as "1.5s", "~2 seconds", "21gt" or "3 redstone ticks".
            A number without a unit is interpreted as seconds.

    Returns:
        The time in game ticks, rounded to the nearest tick, or None if the string is not a recognized time.
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
