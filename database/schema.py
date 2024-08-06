from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict, Literal, Any, get_args, cast, TypeAlias
from database.enums import Status, Category


class UnknownRestrictions(TypedDict, total=False):
    wiring_placement_restrictions: list[str]
    component_restrictions: list[str]


class Info(TypedDict, total=False):
    """A special JSON field in the database that stores various additional information about the build"""

    user: str  # Provided by the submitter if they have any additional information to provide.
    unknown_patterns: list[str]
    unknown_restrictions: UnknownRestrictions


class BuildRecord(TypedDict):
    """A record of a build in the database."""

    id: int
    submission_status: Status
    record_category: RecordCategory | None
    information: Info
    submission_time: str
    edited_time: str
    width: int | None
    height: int | None
    depth: int | None
    completion_time: str | None  # Given by user, not parsable as a datetime
    category: Category
    server_info: dict[str, Any] | None  # JSON
    submitter_id: int | None  # TODO: fix db and remove None


class MessageRecord(TypedDict):
    """A record of a message in the database."""

    message_id: int
    server_id: int
    build_id: int
    channel_id: int
    edited_time: str


class DoorRecord(TypedDict):
    """A record of a door in the database."""

    build_id: int
    orientation: str
    door_width: int | None
    door_height: int | None
    door_depth: int | None
    normal_opening_time: int | None
    normal_closing_time: int | None
    visible_opening_time: int | None
    visible_closing_time: int | None


class ExtenderRecord(TypedDict):
    """A record of an extender in the database."""

    build_id: int


class UtilityRecord(TypedDict):
    """A record of a utility in the database."""

    build_id: int


class EntranceRecord(TypedDict):
    """A record of an entrance in the database."""

    build_id: int


class ServerSettingRecord(TypedDict):
    """A record of a server's setting in the database."""

    server_id: int
    smallest_channel_id: int | None
    fastest_channel_id: int | None
    first_channel_id: int | None
    builds_channel_id: int | None
    voting_channel_id: int | None


DbSettingKey = Literal[
    "smallest_channel_id", "fastest_channel_id", "first_channel_id", "builds_channel_id", "voting_channel_id"
]


class TypeRecord(TypedDict):
    """A record of a type in the database."""

    id: int
    build_category: Category
    name: str


class RestrictionRecord(TypedDict):
    """A restriction on a build."""

    id: int
    build_category: BuildType
    name: str
    type: Restriction


class VersionsRecord(TypedDict):
    """A record of a version in the database"""

    id: int
    edition: str
    major_version: str
    minor_version: str
    patch_number: str
    full_name_temp: str  # TODO: remove


RecordCategory: TypeAlias = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Sequence[RecordCategory] = cast(Sequence[RecordCategory], get_args(RecordCategory))

BuildType: TypeAlias = Literal["Door", "Extender", "Utility", "Entrance"]
BUILD_TYPES: Sequence[BuildType] = cast(Sequence[BuildType], get_args(BuildType))

DoorOrientationName: TypeAlias = Literal["Door", "Skydoor", "Trapdoor"]
DOOR_ORIENTATION_NAMES = cast(Sequence[DoorOrientationName], get_args(DoorOrientationName))

ChannelPurpose: TypeAlias = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
CHANNEL_PURPOSES = cast(Sequence[ChannelPurpose], get_args(ChannelPurpose))

Restriction = Literal["wiring-placement", "component", "miscellaneous"]
RESTRICTIONS = cast(Sequence[Restriction], get_args(Restriction))
