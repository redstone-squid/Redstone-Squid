import logging
import os
import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal, TypeAlias, TypedDict, cast, get_args

from pgvector.sqlalchemy import VECTOR
from pydantic.types import Json
from sqlalchemy import (
    ARRAY,
    JSON,
    TIMESTAMP,
    UUID,
    BigInteger,
    Boolean,
    Double,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
)
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)


# AIDEV-NOTE: SQLAlchemy table definitions for gradual migration from Supabase
class Base(MappedAsDataclass, DeclarativeBase):
    pass


class User(Base):
    """A user in the system, which can be linked to both Discord and Minecraft accounts."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    discord_id: Mapped[int | None] = mapped_column(BigInteger)
    minecraft_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ign: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), default=func.now())

    build_creators: Mapped[list[BuildCreator]] = relationship(back_populates="user", default_factory=list)
    builds: AssociationProxy[list[Build]] = association_proxy("build_creators", "build", default_factory=list)


class Version(Base):
    """A version of Minecraft that a build is compatible with."""

    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    edition: Mapped[str] = mapped_column(String, nullable=False)
    major_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minor_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patch_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

RecordCategory: TypeAlias = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Sequence[RecordCategory] = cast(Sequence[RecordCategory], get_args(RecordCategory))

BuildType: TypeAlias = Literal["Door", "Extender", "Utility", "Entrance"]
BUILD_TYPES: Sequence[BuildType] = cast(Sequence[BuildType], get_args(BuildType))

DoorOrientationName: TypeAlias = Literal["Door", "Skydoor", "Trapdoor"]
DOOR_ORIENTATION_NAMES = cast(Sequence[DoorOrientationName], get_args(DoorOrientationName))

Restriction = Literal["wiring-placement", "component", "miscellaneous"]
RESTRICTIONS = cast(Sequence[Restriction], get_args(Restriction))

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


class Restriction(Base):
    """A restriction that can be applied to builds."""

    __tablename__ = "restrictions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    build_category: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String, unique=True)
    type: Mapped[str | None] = mapped_column(String)

    build_restrictions: Mapped[list[BuildRestriction]] = relationship(
        back_populates="restriction", default_factory=list
    )
    builds: AssociationProxy[list[Build]] = association_proxy("build_restrictions", "build", default_factory=list)

    aliases: Mapped[list[RestrictionAlias]] = relationship(back_populates="restriction", default_factory=list)


class RestrictionAlias(Base):
    """An alias for a restriction, allowing for alternative names."""

    __tablename__ = "restriction_aliases"
    restriction_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("restrictions.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String, nullable=False, unique=True, primary_key=True)
    restriction: Mapped[Restriction] = relationship(back_populates="aliases")  # note: backref
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=func.now())


class Type(Base):
    """A build pattern."""

    __tablename__ = "types"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    build_category: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String, unique=True)  # FIXME: This should be unique per build category


class Build(Base):
    """A build submitted by a user."""

    __tablename__ = "builds"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    submission_status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    record_category: Mapped[str | None] = mapped_column(String)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    depth: Mapped[int | None] = mapped_column(Integer)
    completion_time: Mapped[str | None] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)
    submitter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_message_id: Mapped[int | None] = mapped_column(BigInteger)
    version_spec: Mapped[str | None] = mapped_column(String)
    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(int(os.getenv("EMBEDDING_DIMENSION", "1536"))), nullable=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    extra_info: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default_factory=dict)
    submission_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), default=func.now())
    edited_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=func.now())
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    build_creators: Mapped[list[BuildCreator]] = relationship(back_populates="build", default_factory=list)
    creators: AssociationProxy[list[User]] = association_proxy("build_creators", "user", default_factory=list)

    build_restrictions: Mapped[list[BuildRestriction]] = relationship(back_populates="build", default_factory=list)
    restrictions: AssociationProxy[list[Restriction]] = association_proxy(
        "build_restrictions", "restriction", default_factory=list
    )

    build_vote_sessions: Mapped[list[BuildVoteSession]] = relationship(back_populates="build", default_factory=list)
    vote_sessions: AssociationProxy[list[VoteSession]] = association_proxy(
        "build_vote_sessions", "vote_session", default_factory=list
    )

    links: Mapped[list[BuildLink]] = relationship(back_populates="build", default_factory=list)
    messages: Mapped[list[Message]] = relationship(back_populates="build", default_factory=list)
    door: Mapped[Door | None] = relationship(back_populates="build", uselist=False, default=None)
    extender: Mapped[Extender | None] = relationship(back_populates="build", uselist=False, default=None)
    utility: Mapped[Utility | None] = relationship(back_populates="build", uselist=False, default=None)
    entrance: Mapped[Entrance | None] = relationship(back_populates="build", uselist=False, default=None)


class Message(Base):
    """A message associated with a build or vote session."""

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    build_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("builds.id"))
    vote_session_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vote_sessions.id"))
    content: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=func.now())

    build: Mapped[Build | None] = relationship(back_populates="messages", default=None)
    vote_session: Mapped[VoteSession | None] = relationship(back_populates="messages", default=None)


class Door(Base):
    """A door build with specific dimensions and timing information."""

    __tablename__ = "doors"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    orientation: Mapped[str] = mapped_column(String, nullable=False)
    door_width: Mapped[int] = mapped_column(Integer, nullable=False)
    door_height: Mapped[int] = mapped_column(Integer, nullable=False)
    door_depth: Mapped[int | None] = mapped_column(Integer)
    normal_opening_time: Mapped[int | None] = mapped_column(BigInteger)
    normal_closing_time: Mapped[int | None] = mapped_column(BigInteger)
    visible_opening_time: Mapped[int | None] = mapped_column(BigInteger)
    visible_closing_time: Mapped[int | None] = mapped_column(BigInteger)

    build: Mapped[Build] = relationship(back_populates="door")


class Extender(Base):
    """An extender build."""

    __tablename__ = "extenders"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="extender")


class Utility(Base):
    """A utility build."""

    __tablename__ = "utilities"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="utility")


class Entrance(Base):
    """An entrance build."""

    __tablename__ = "entrances"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="entrance")


class BuildCreator(Base):
    """Association table between builds and their creators."""

    __tablename__ = "build_creators"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="build_creators")
    user: Mapped[User] = relationship(back_populates="build_creators")


class BuildRestriction(Base):
    """Association table between builds and their restrictions."""

    __tablename__ = "build_restrictions"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    restriction_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("restrictions.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="build_restrictions")
    restriction: Mapped[Restriction] = relationship(back_populates="build_restrictions")


MediaType = Literal["image", "video", "world-download"]


class BuildLink(Base):
    """A link associated with a build (image, video, world download)."""

    __tablename__ = "build_links"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    url: Mapped[str] = mapped_column(String, nullable=False, primary_key=True)
    media_type: Mapped[MediaType | None] = mapped_column(String)  # TODO: nullable)

    build: Mapped[Build] = relationship(back_populates="links")


class ServerSetting(Base):
    """Settings for a Discord server."""

    __tablename__ = "server_settings"
    server_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    smallest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    fastest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    first_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    builds_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    voting_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    staff_roles_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    trusted_roles_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    in_server: Mapped[bool] = mapped_column(Boolean)


class VerificationCode(Base):
    """A verification code for linking Minecraft accounts."""

    __tablename__ = "verification_codes"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    minecraft_uuid: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False, default="")
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), nullable=False, default=func.now())
    expires: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, default=func.now() + text("INTERVAL '10 minutes'")
    )


class VoteSession(Base):
    """A voting session for builds or log deletions."""

    __tablename__ = "vote_sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    pass_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    fail_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=func.now())

    build_vote_sessions: Mapped[list[BuildVoteSession]] = relationship(
        back_populates="vote_session", default_factory=list
    )
    builds: AssociationProxy[list[Build]] = association_proxy("build_vote_sessions", "build", default_factory=list)

    messages: Mapped[list[Message]] = relationship(back_populates="vote_session", default_factory=list)
    votes: Mapped[list[Vote]] = relationship(back_populates="vote_session", default_factory=list)
    delete_log_vote_sessions: Mapped[list[DeleteLogVoteSession]] = relationship(
        back_populates="vote_session", default_factory=list
    )


class BuildVoteSession(Base):
    """Association table between builds and vote sessions."""

    __tablename__ = "build_vote_sessions"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vote_sessions.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("builds.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    changes: Mapped[list[Any]] = mapped_column(JSON, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="build_vote_sessions")
    build: Mapped[Build] = relationship(back_populates="build_vote_sessions")


class DeleteLogVoteSession(Base):
    """Association table between vote sessions and messages to be deleted."""

    __tablename__ = "delete_log_vote_sessions"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vote_sessions.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    target_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="delete_log_vote_sessions")


class Vote(Base):
    """A vote cast in a vote session."""

    __tablename__ = "votes"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vote_sessions.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    weight: Mapped[float | None] = mapped_column(Double)

    vote_session: Mapped[VoteSession] = relationship(back_populates="votes")


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
    original_message_id: int | None
    version_spec: str
    ai_generated: bool
    embedding: list[float] | None
    is_locked: bool
    locked_at: str | None  # timestamptz


class MessageRecord(TypedDict):
    """A record of a message in the database."""

    id: int
    updated_at: str
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
    in_server: bool


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
    type: RestrictionType


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
