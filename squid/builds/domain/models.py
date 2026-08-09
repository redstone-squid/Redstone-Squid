"""Build domain entity and value objects."""

import re
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import IntEnum, StrEnum
from functools import cached_property
from typing import (
    Any,
    Final,
    Literal,
    Self,
    TypeAlias,
    TypedDict,
    cast,
    get_args,
    overload,
)

from whenever import Instant

from squid.builds.errors import InvalidBuildError
from squid.core.errors import DataIntegrityError
from squid.tags.domain import TagAssignment

RecordCategoryLiteral: TypeAlias = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Sequence[RecordCategoryLiteral] = cast(
    Sequence[RecordCategoryLiteral], get_args(RecordCategoryLiteral)
)
BuildCategoryLiteral: TypeAlias = Literal["Door", "Extender", "Utility", "Entrance"]
BUILD_TYPES: Sequence[BuildCategoryLiteral] = cast(Sequence[BuildCategoryLiteral], get_args(BuildCategoryLiteral))
DoorOrientationLiteral: TypeAlias = Literal["Door", "Skydoor", "Trapdoor"]
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
            msg = f"Attribute `{self._private_name[1:]}` is immutable!"
            raise TypeError(msg) from None

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
        msg = f"{cls} is not a dataclass"
        raise TypeError(msg)

    params = cls.__dataclass_params__  # type: ignore
    # _DataclassParams(init=True,repr=True,eq=True,order=True,unsafe_hash=False,
    #                   frozen=True,match_args=True,kw_only=False,slots=False,
    #                   weakref_slot=False)
    if params.frozen:
        return cls

    for f in fields(cls):  # type: ignore
        if "frozen" in f.metadata:
            setattr(cls, f.name, FrozenField(f.name))
    return cls


@freeze_fields
@dataclass
class Build:
    """A submission to the database.

    This is a very large class, the methods are ordered as follows:
    - Static constructors
    - Magic (dunder) methods
    - Properties
    - Normal methods
    - load(), save() and the helper methods it calls

    Locking:
        A build can be locked to prevent concurrent modifications.
        This lock is a simple boolean in the database, but is implemented as a counter in the object to allow nested locks (reentrant locks).
    """

    id: int | None = None
    revision: int = 1
    submission_status: Status | None = None
    category: BuildCategory | None = None
    record_category: RecordCategoryLiteral | None = None
    versions: list[str] = field(default_factory=list)
    version_spec: str | None = None

    width: int | None = None
    height: int | None = None
    depth: int | None = None

    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None

    door_type: list[str] = field(default_factory=list)
    door_orientation_type: DoorOrientationLiteral | None = None

    wiring_placement_restrictions: list[str] = field(default_factory=list)
    animated_restrictions: list[str] = field(default_factory=list)
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)
    tags: list[TagAssignment] = field(default_factory=list)

    extender_orientation: str | None = None
    extension_length: int | None = None
    extender_type: str | None = None

    normal_closing_time: int | None = None
    normal_opening_time: int | None = None
    visible_closing_time: int | None = None
    visible_opening_time: int | None = None

    extra_info: Info = field(default_factory=Info)
    creators_ign: list[str] = field(default_factory=list)

    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    world_download_urls: list[str] = field(default_factory=list)
    schematic_urls: list[str] = field(default_factory=list)
    render_urls: list[str] = field(default_factory=list)

    submitter_id: int | None = None
    # TODO: save the submitted time too
    completion_time: str | None = None
    completion_at: Instant | None = None
    completion_evidence: str | None = None
    description: str | None = None
    submission_time: Instant | None = None
    edited_time: Instant | None = None

    original_server_id: Final[int | None] = frozen_field(default=None)
    original_channel_id: Final[int | None] = frozen_field(default=None)
    original_message_id: Final[int | None] = frozen_field(default=None)
    original_message_author_id: Final[int | None] = frozen_field(default=None)
    original_message: Final[str | None] = frozen_field(default=None)

    ai_generated: bool | None = None
    embedding: list[float] | None = field(default=None, repr=False)

    @cached_property
    def original_link(self) -> str | None:
        """The link to the original message of the build."""
        if self.original_message_id and self.original_channel_id:
            if self.original_server_id is None:
                msg = "This message is from DMs."
                raise NotImplementedError(msg)
            return f"https://discord.com/channels/{self.original_server_id}/{self.original_channel_id}/{self.original_message_id}"
        return None

    @property
    def dimensions(self) -> tuple[int | None, int | None, int | None]:
        """The dimensions of the build."""
        return self.width, self.height, self.depth

    @dimensions.setter
    def dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.width, self.height, self.depth = dimensions

    @property
    def door_dimensions(self) -> tuple[int | None, int | None, int | None]:
        """The dimensions of the door (hallway)."""
        return self.door_width, self.door_height, self.door_depth

    @door_dimensions.setter
    def door_dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.door_width, self.door_height, self.door_depth = dimensions

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
        """The restrictions of the build."""
        return {
            "wiring_placement_restrictions": self.wiring_placement_restrictions,
            "animated_restrictions": self.animated_restrictions,
            "component_restrictions": self.component_restrictions,
            "miscellaneous_restrictions": self.miscellaneous_restrictions,
        }

    @restrictions.setter
    async def restrictions(
        self,
        restrictions: dict[
            Literal[
                "wiring_placement_restrictions",
                "animated_restrictions",
                "component_restrictions",
                "miscellaneous_restrictions",
            ],
            Sequence[str] | None,
        ],
    ) -> None:
        """Sets the restrictions of the build."""
        self.wiring_placement_restrictions = list(restrictions.get("wiring_placement_restrictions") or [])
        self.animated_restrictions = list(restrictions.get("animated_restrictions") or [])
        self.component_restrictions = list(restrictions.get("component_restrictions") or [])
        self.miscellaneous_restrictions = list(restrictions.get("miscellaneous_restrictions") or [])

    def classify_restrictions(
        self,
        restrictions: Sequence[str],
        definitions: Mapping[str, RestrictionTypeLiteral | None],
    ) -> None:
        """Replace restrictions using already-loaded classification metadata."""
        self.wiring_placement_restrictions = []
        self.animated_restrictions = []
        self.component_restrictions = []
        self.miscellaneous_restrictions = []

        definitions_by_name: dict[str, tuple[str, RestrictionTypeLiteral | None]] = {
            name.lower(): (name, restriction_type) for name, restriction_type in definitions.items()
        }
        bucket: dict[RestrictionTypeLiteral, list[str]] = {
            "wiring-placement": self.wiring_placement_restrictions,
            "animated": self.animated_restrictions,
            "component": self.component_restrictions,
            "miscellaneous": self.miscellaneous_restrictions,
        }

        for restriction in restrictions:
            definition = definitions_by_name.get(restriction.lower())
            if definition is None:
                continue
            canonical_name, restriction_type = definition
            if restriction_type is None:
                msg = "The type is supposed to never be None, this is a bug in the database."
                raise DataIntegrityError(msg, context={"restriction": canonical_name})
            bucket[restriction_type].append(canonical_name)

    @property
    def title(self) -> str:
        """The user-facing title, including individual-build UX decoration."""
        from squid.builds.domain.titles import format_build_display_title

        return format_build_display_title(self, markdown=True)

    def diff[T: Any](self, other: "Build", *, allow_different_id: bool = False) -> list[tuple[str, T, T]]:
        """
        Returns the differences between this build and another

        Args:
            other: Another build to compare to.
            allow_different_id: Whether the ID of the builds can be different.

        Returns:
            A list of tuples containing the attribute name, the value of this build, and the value of the other build.

        Raises:
            ValueError: If the IDs of the builds are different and allow_different_id is False.
        """
        if self.id != other.id and not allow_different_id:
            msg = "The IDs of the builds are different."
            raise InvalidBuildError(msg, context={"left_id": self.id, "right_id": other.id})

        differences: list[tuple[str, T, T]] = []
        # TODO: too much magic, try using __dataclass_fields__ or just listing the fields manually
        for attr in [a for a in dir(self) if not a.startswith("__") and not callable(getattr(self, a))]:
            if attr == "id":
                continue
            if getattr(self, attr) != getattr(other, attr):
                differences.append((attr, getattr(self, attr), getattr(other, attr)))

        return differences

    @staticmethod
    def get_attr_type(attribute: str) -> type:
        """Gets the type of the attribute in the Build class."""
        if attribute in Build.__annotations__:
            attr_type = typing.get_type_hints(Build)[attribute]
        else:
            try:
                cls_attr = getattr(Build, attribute)
                if isinstance(cls_attr, property):
                    attr_type = typing.get_type_hints(cls_attr.fget)["return"]
                else:
                    msg = "Not sure how to automatically get the type of this attribute."
                    raise NotImplementedError(msg)
            except AttributeError as err:
                msg = f"Attribute {attribute} is not in the Build class."
                raise InvalidBuildError(msg, context={"attribute": attribute}) from err
        return attr_type


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
