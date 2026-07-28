"""Submitting and retrieving submissions to/from the database"""

import re
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime
from functools import cached_property
from typing import Any, Final, Literal, Self, overload

from squid.db.schema import (
    BuildCategory,
    BuildRecord,
    DoorOrientationLiteral,
    DoorRecord,
    EntranceRecord,
    ExtenderRecord,
    Info,
    LinkRecord,
    MessageRecord,
    RecordCategoryLiteral,
    RestrictionRecord,
    RestrictionTypeLiteral,
    Status,
    TypeRecord,
    UserRecord,
    UtilityRecord,
    VersionRecord,
)


class JoinedBuildRecord(BuildRecord):
    """Represents a build record with all the columns joined."""

    versions: list[VersionRecord]
    build_links: list[LinkRecord]
    build_creators: list[dict[str, Any]]  # You want to use users instead. This is just a join table.
    users: list[UserRecord]
    types: list[TypeRecord]
    restrictions: list[RestrictionRecord]
    doors: DoorRecord | None
    extenders: ExtenderRecord | None
    utilities: UtilityRecord | None
    entrances: EntranceRecord | None
    messages: MessageRecord | None  # Not actually all the associated messages, just the original message


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


# https://stackoverflow.com/questions/74714300/paramspec-for-a-pre-defined-function-without-using-generic-callablep
def signature_from[**P, T](_original: Callable[P, T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Copies the signature of a function to another function."""

    def _decorator(func: Callable[P, T]) -> Callable[P, T]:
        return func

    return _decorator


@signature_from(field)
def frozen_field(**kwargs: Any):
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
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)

    normal_closing_time: int | None = None
    normal_opening_time: int | None = None
    visible_closing_time: int | None = None
    visible_opening_time: int | None = None

    extra_info: Info = field(default_factory=Info)
    creators_ign: list[str] = field(default_factory=list)

    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    world_download_urls: list[str] = field(default_factory=list)

    submitter_id: int | None = None
    # TODO: save the submitted time too
    completion_time: str | None = None
    edited_time: datetime | None = None

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
        Literal["wiring_placement_restrictions", "component_restrictions", "miscellaneous_restrictions"],
        Sequence[str] | None,
    ]:
        """The restrictions of the build."""
        return {
            "wiring_placement_restrictions": self.wiring_placement_restrictions,
            "component_restrictions": self.component_restrictions,
            "miscellaneous_restrictions": self.miscellaneous_restrictions,
        }

    @restrictions.setter
    async def restrictions(
        self,
        restrictions: dict[
            Literal["wiring_placement_restrictions", "component_restrictions", "miscellaneous_restrictions"],
            Sequence[str] | None,
        ],
    ) -> None:
        """Sets the restrictions of the build."""
        self.wiring_placement_restrictions = list(restrictions.get("wiring_placement_restrictions") or [])
        self.component_restrictions = list(restrictions.get("component_restrictions") or [])
        self.miscellaneous_restrictions = list(restrictions.get("miscellaneous_restrictions") or [])

    def classify_restrictions(
        self,
        restrictions: Sequence[str],
        definitions: Mapping[str, RestrictionTypeLiteral | None],
    ) -> None:
        """Replace restrictions using already-loaded classification metadata."""
        self.wiring_placement_restrictions = []
        self.component_restrictions = []
        self.miscellaneous_restrictions = []

        definitions_by_name: dict[str, tuple[str, RestrictionTypeLiteral | None]] = {
            name.lower(): (name, restriction_type) for name, restriction_type in definitions.items()
        }
        bucket: dict[RestrictionTypeLiteral, list[str]] = {
            "wiring-placement": self.wiring_placement_restrictions,
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
                raise RuntimeError(msg)
            bucket[restriction_type].append(canonical_name)

    @property
    def title(self) -> str:
        """The official Redstone Squid defined title for the build."""
        title = ""

        if self.category != "Door":
            msg = "Only doors are supported for now."
            raise NotImplementedError(msg)

        if self.submission_status == Status.PENDING:
            title += "Pending: "
        elif self.submission_status == Status.DENIED:
            title += "Denied: "
        if self.ai_generated:
            title += "\N{ROBOT FACE}"
        if self.record_category:
            title += f"{self.record_category} "

        # Special casing misc restrictions shaped like "0.3s" and "524 Blocks"
        for restriction in self.extra_info.get("unknown_restrictions", {}).get("miscellaneous_restrictions", []):
            if re.match(r"\d+\.\d+\s*s", restriction) or re.match(r"\d+\s*[Bb]locks", restriction):
                title += f"{restriction} "

        # FIXME: This is included in the title for now to match people's expectations
        for restriction in self.component_restrictions:
            title += f"{restriction} "
        for restriction in self.extra_info.get("unknown_restrictions", {}).get("component_restrictions", []):
            title += f"*{restriction}* "

        # Door dimensions
        if self.door_width and self.door_height and self.door_depth and self.door_depth > 1:
            title += f"{self.door_width}x{self.door_height}x{self.door_depth} "
        elif self.door_width and self.door_height:
            title += f"{self.door_width}x{self.door_height} "
        elif self.door_width:
            title += f"{self.door_width} Wide "
        elif self.door_height:
            title += f"{self.door_height} High "

        # Wiring Placement Restrictions
        for restriction in self.wiring_placement_restrictions:
            title += f"{restriction} "

        for restriction in self.extra_info.get("unknown_restrictions", {}).get("wiring_placement_restrictions", []):
            title += f"*{restriction}* "

        # Pattern
        for pattern in self.door_type:
            if pattern != "Regular":
                title += f"{pattern} "

        for pattern in self.extra_info.get("unknown_patterns", []):
            title += f"*{pattern}* "

        # Door type
        if self.door_orientation_type is None:
            msg = "Door orientation type information (i.e. Door/Trapdoor/Skydoor) is missing."
            raise ValueError(msg)
        title += self.door_orientation_type

        return title

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
            raise ValueError(msg)

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
                raise ValueError(msg) from err
        return attr_type
