from collections.abc import Sequence
from enum import IntEnum, StrEnum
from typing import Literal, TypeAlias, TypedDict, cast, get_args

from pydantic.types import Json

RecordCategory: TypeAlias = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Sequence[RecordCategory] = cast(Sequence[RecordCategory], get_args(RecordCategory))

BuildType: TypeAlias = Literal["Door", "Extender", "Utility", "Entrance"]
BUILD_TYPES: Sequence[BuildType] = cast(Sequence[BuildType], get_args(BuildType))

DoorOrientationName: TypeAlias = Literal["Door", "Skydoor", "Trapdoor"]
DOOR_ORIENTATION_NAMES = cast(Sequence[DoorOrientationName], get_args(DoorOrientationName))

RestrictionStr = Literal["wiring-placement", "component", "miscellaneous"]
RESTRICTIONS = cast(Sequence[RestrictionStr], get_args(RestrictionStr))

MessagePurpose = Literal["view_pending_build", "view_confirmed_build", "vote", "build_original_message"]

VoteKind = Literal["build", "delete_log"]

# Make sure you also update _SETTING_TO_DB_KEY in database/server_settings.py
DbSettingKey = Literal[
    "smallest_channel_id",
    "fastest_channel_id",
    "first_channel_id",
    "builds_channel_id",
    "voting_channel_id",
    "staff_roles_ids",
    "trusted_roles_ids",
]

Setting: TypeAlias = Literal["Smallest", "Fastest", "First", "Builds", "Vote", "Staff", "Trusted"]
SETTINGS = cast(Sequence[Setting], get_args(Setting))
assert len(SETTINGS) == len(get_args(DbSettingKey)), "DbSetting and Setting do not have the same number of elements."


class UnknownRestrictions(TypedDict, total=False):
    wiring_placement_restrictions: list[str]
    component_restrictions: list[str]
    miscellaneous_restrictions: list[str]


class ServerInfo(TypedDict, total=False):
    """Various additional information about the server"""

    server_ip: str
    coordinates: str
    command_to_build: str


class Info(TypedDict, total=False):
    """A special JSON field in the database that stores various additional information about the build"""

    user: str  # Provided by the submitter if they have any additional information to provide.
    unknown_patterns: list[str]
    unknown_restrictions: UnknownRestrictions
    server_info: ServerInfo


class Status(IntEnum):
    """The status of a submission."""

    PENDING = 0
    CONFIRMED = 1
    DENIED = 2


class Category(StrEnum):
    """The categories of the builds."""

    DOOR = "Door"
    EXTENDER = "Extender"
    UTILITY = "Utility"
    ENTRANCE = "Entrance"


class BuildRecord(TypedDict):
    """A record of a build in the database."""

    id: int
    submission_status: Status
    record_category: RecordCategory | None
    extra_info: Info
    submission_time: str
    edited_time: str
    width: int | None
    height: int | None
    depth: int | None
    completion_time: str | None  # Given by user, not parsable as a datetime
    category: Category
    submitter_id: int
    original_message_id: int
    version_spec: str
    ai_generated: bool
    embedding: list[float] | None
    is_locked: bool
    locked_at: str | None  # timestamptz


class MessageRecord(TypedDict):
    """A record of a message in the database."""

    id: int
    edited_time: str
    server_id: int
    channel_id: int
    author_id: int
    purpose: MessagePurpose
    build_id: int | None
    vote_session_id: int | None
    content: str | None


class DoorRecord(TypedDict):
    """A record of a door in the database."""

    build_id: int
    orientation: DoorOrientationName
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
    staff_roles_ids: list[int] | None
    trusted_roles_ids: list[int] | None


class LinkRecord(TypedDict):
    """A record of a link in the database."""

    build_id: int
    url: str
    media_type: Literal["image", "video", "world-download"]


class UserRecord(TypedDict):
    """A record of a user in the database."""

    id: int
    discord_id: int | None
    minecraft_uuid: str | None
    ign: str
    created_at: str


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
    type: RestrictionStr


class RestrictionAliasRecord(TypedDict):
    """An alias for a restriction on a build."""

    restriction_id: int
    alias: str
    created_at: str


class VersionRecord(TypedDict):
    """A record of a version in the database"""

    id: int
    edition: str
    major_version: int
    minor_version: int
    patch_number: int


class QuantifiedVersionRecord(TypedDict):
    """A record of a quantified version in the database. This is obtained by calling the get_quantified_version_names RPC."""

    id: int
    quantified_name: str


class VoteSessionRecord(TypedDict):
    """A record of a vote session in the database."""

    id: int
    created_at: str
    status: Literal["open", "closed"]
    author_id: int
    kind: str
    pass_threshold: int
    fail_threshold: int


class BuildVoteSessionRecord(TypedDict):
    """A record of a build vote session in the database."""

    vote_session_id: int
    build_id: int
    changes: Json[list]


class DeleteLogVoteSessionRecord(TypedDict):
    """A record of a delete log vote session in the database."""

    vote_session_id: int
    target_message_id: int
    target_channel_id: int
    target_server_id: int
