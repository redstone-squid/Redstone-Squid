import logging
import os
import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal, TypeAlias, TypedDict, cast, get_args

from pgvector.sqlalchemy import VECTOR
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
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
)
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)


RecordCategory: TypeAlias = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Sequence[RecordCategory] = cast(Sequence[RecordCategory], get_args(RecordCategory))

BuildTypeStr: TypeAlias = Literal["Door", "Extender", "Utility", "Entrance"]
BUILD_TYPES: Sequence[BuildTypeStr] = cast(Sequence[BuildTypeStr], get_args(BuildTypeStr))

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


# AIDEV-NOTE: SQLAlchemy table definitions for gradual migration from Supabase
class Base(AsyncAttrs, MappedAsDataclass, DeclarativeBase):
    pass


class User(Base):
    """A user in the system, which can be linked to both Discord and Minecraft accounts."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    ign: Mapped[str] = mapped_column(String, default=None)
    discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    minecraft_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), default=func.now())

    build_creators: Mapped[list["BuildCreator"]] = relationship(back_populates="user", default_factory=list, lazy="raise_on_sql")
    builds: AssociationProxy[list["Build"]] = association_proxy("build_creators", "build", default_factory=list)


class Version(Base):
    """A version of Minecraft that a build is compatible with."""

    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    edition: Mapped[str] = mapped_column(String, nullable=False)
    major_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minor_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patch_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    build_versions: Mapped[list["BuildVersion"]] = relationship(back_populates="version", default_factory=list, lazy="raise_on_sql")
    builds: AssociationProxy[list["Build"]] = association_proxy("build_versions", "build", default_factory=list)


class Restriction(Base):
    """A restriction that can be applied to builds."""

    __tablename__ = "restrictions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    build_category: Mapped[BuildTypeStr | None] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, unique=True)  # FIXME: Shouldn't be nullable, note that to make type checkers happy I made this Mapped[str] instead of Mapped[str | None], even though it is nullable in the database
    type: Mapped[RestrictionStr | None] = mapped_column(String)

    build_restrictions: Mapped[list["BuildRestriction"]] = relationship(
        back_populates="restriction", default_factory=list, lazy="raise_on_sql"
    )
    builds: AssociationProxy[list["Build"]] = association_proxy("build_restrictions", "build", default_factory=list)

    aliases: Mapped[list["RestrictionAlias"]] = relationship(back_populates="restriction", default_factory=list, lazy="selectin")


class RestrictionAlias(Base):
    """An alias for a restriction, allowing for alternative names."""

    __tablename__ = "restriction_aliases"
    restriction_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("restrictions.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String, nullable=False, unique=True, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=func.now())

    restriction: Mapped[Restriction] = relationship(back_populates="aliases", init=False, lazy="joined")


class Type(Base):
    """A build pattern."""

    __tablename__ = "types"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    build_category: Mapped[BuildTypeStr | None] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, unique=True)  # FIXME: This should be unique per build category  # FIXME: shouldn't be nullable

    build_types: Mapped[list["BuildType"]] = relationship(back_populates="type", default_factory=list, lazy="selectin")
    builds: AssociationProxy[list["Build"]] = association_proxy("build_types", "build", default_factory=list)


class Build(Base):
    """A build submitted by a user."""

    __tablename__ = "builds"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    submission_status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    record_category: Mapped[RecordCategory | None] = mapped_column(String)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    depth: Mapped[int | None] = mapped_column(Integer)
    completion_time: Mapped[str | None] = mapped_column(String)  # Given by user, not parsable as a datetime
    category: Mapped[BuildTypeStr | None] = mapped_column(String)
    submitter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_message_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("messages.id"))
    original_message: Mapped["Message | None"] = relationship(
        back_populates="build", foreign_keys="Build.original_message_id", uselist=False, default=None, lazy="joined"
    )
    version_spec: Mapped[str | None] = mapped_column(String, default=None)
    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(int(os.getenv("EMBEDDING_DIMENSION", "1536"))), default=None
    )
    locked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_info: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default_factory=dict)
    submission_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), default=func.now())
    edited_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=func.now())
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    build_creators: Mapped[list["BuildCreator"]] = relationship(back_populates="build", default_factory=list, lazy="selectin")
    creators: AssociationProxy[list[User]] = association_proxy("build_creators", "user", default_factory=list)

    build_restrictions: Mapped[list["BuildRestriction"]] = relationship(back_populates="build", default_factory=list, lazy="selectin")
    restrictions: AssociationProxy[list[Restriction]] = association_proxy(
        "build_restrictions", "restriction", default_factory=list
    )

    build_versions: Mapped[list["BuildVersion"]] = relationship(back_populates="build", default_factory=list, lazy="selectin")
    versions: AssociationProxy[list[Version]] = association_proxy("build_versions", "version", default_factory=list)

    build_types: Mapped[list["BuildType"]] = relationship(back_populates="build", default_factory=list, lazy="selectin")
    types: AssociationProxy[list[Type]] = association_proxy("build_types", "type", default_factory=list)

    build_vote_sessions: Mapped[list["BuildVoteSession"]] = relationship(back_populates="build", default_factory=list, lazy="raise_on_sql")
    vote_sessions: AssociationProxy[list["VoteSession"]] = association_proxy(
        "build_vote_sessions", "vote_session", default_factory=list
    )

    links: Mapped[list["BuildLink"]] = relationship(back_populates="build", default_factory=list, lazy="selectin")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="build", foreign_keys="Message.build_id", default_factory=list, lazy="raise_on_sql"
    )
    door: Mapped["Door | None"] = relationship(back_populates="build", uselist=False, default=None, lazy="joined")
    extender: Mapped["Extender | None"] = relationship(back_populates="build", uselist=False, default=None, lazy="joined")
    utility: Mapped["Utility | None"] = relationship(back_populates="build", uselist=False, default=None, lazy="joined")
    entrance: Mapped["Entrance | None"] = relationship(back_populates="build", uselist=False, default=None, lazy="joined")

    __mapper_args__ = {
        "polymorphic_on": category,
    }


class Message(Base):
    """A message associated with a build or vote session."""

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # init=True because this is the message ID, which should be known when creating the object
    server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    build_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("builds.id"))
    vote_session_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vote_sessions.id"))
    content: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=func.now())

    build: Mapped[Build | None] = relationship(back_populates="messages", foreign_keys="Message.build_id", default=None, lazy="joined")
    vote_session: Mapped["VoteSession | None"] = relationship(back_populates="messages", default=None, lazy="joined")


class Door(Build):
    """A door build with specific dimensions and timing information."""

    __tablename__ = "doors"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Door",
    }
    
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    orientation: Mapped[DoorOrientationName] = mapped_column(String, nullable=False)
    door_width: Mapped[int] = mapped_column(Integer, nullable=False)
    door_height: Mapped[int] = mapped_column(Integer, nullable=False)
    door_depth: Mapped[int | None] = mapped_column(Integer)
    normal_opening_time: Mapped[int | None] = mapped_column(BigInteger)
    normal_closing_time: Mapped[int | None] = mapped_column(BigInteger)
    visible_opening_time: Mapped[int | None] = mapped_column(BigInteger)
    visible_closing_time: Mapped[int | None] = mapped_column(BigInteger)

    build: Mapped[Build] = relationship(back_populates="door", lazy="joined")


class Extender(Build):
    """An extender build."""

    __tablename__ = "extenders"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Extender",
    }
    
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="extender", lazy="joined")


class Utility(Build):
    """A utility build."""

    __tablename__ = "utilities"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Utility",
    }
    
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="utility", lazy="joined")


class Entrance(Build):
    """An entrance build."""

    __tablename__ = "entrances"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Entrance",
    }
    
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="entrance", lazy="joined")


class BuildCreator(Base):
    """Association table between builds and their creators."""

    __tablename__ = "build_creators"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="build_creators", lazy="joined")
    user: Mapped[User] = relationship(back_populates="build_creators", lazy="joined")


class BuildRestriction(Base):
    """Association table between builds and their restrictions."""

    __tablename__ = "build_restrictions"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    restriction_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("restrictions.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="build_restrictions", lazy="joined")
    restriction: Mapped[Restriction] = relationship(back_populates="build_restrictions", lazy="joined")


class BuildVersion(Base):
    """Association table between builds and their versions."""

    __tablename__ = "build_versions"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    version_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("versions.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="build_versions", lazy="joined")
    version: Mapped[Version] = relationship(back_populates="build_versions", lazy="joined")


class BuildType(Base):
    """Association table between builds and their types."""

    __tablename__ = "build_types"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    type_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("types.id"), primary_key=True)

    build: Mapped[Build] = relationship(back_populates="build_types", lazy="joined")
    type: Mapped[Type] = relationship(back_populates="build_types", lazy="joined")


MediaType = Literal["image", "video", "world-download"]


class BuildLink(Base):
    """A link associated with a build (image, video, world download)."""

    __tablename__ = "build_links"
    build_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("builds.id"), primary_key=True)
    url: Mapped[str] = mapped_column(String, nullable=False, primary_key=True)
    media_type: Mapped[MediaType | None] = mapped_column(String)  # TODO: nullable)

    build: Mapped[Build] = relationship(back_populates="links", lazy="joined")


class ServerSetting(Base):
    """Settings for a Discord server."""

    __tablename__ = "server_settings"
    server_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    smallest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    fastest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    first_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    builds_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    voting_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    staff_roles_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default_factory=list)
    trusted_roles_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default_factory=list)
    in_server: Mapped[bool] = mapped_column(Boolean, default=False)


class VerificationCode(Base):
    """A verification code for linking Minecraft accounts."""

    __tablename__ = "verification_codes"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    minecraft_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
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

    build_vote_sessions: Mapped["BuildVoteSession | None"] = relationship(
        back_populates="vote_session", default=None, uselist=False, lazy="joined"
    )
    builds: AssociationProxy[Build] = association_proxy("build_vote_sessions", "build", default_factory=list)

    messages: Mapped[list[Message]] = relationship(back_populates="vote_session", default_factory=list, lazy="selectin")
    votes: Mapped[list["Vote"]] = relationship(back_populates="vote_session", default_factory=list, lazy="selectin")
    delete_log_vote_sessions: Mapped["DeleteLogVoteSession | None"] = relationship(
        back_populates="vote_session", default=None, uselist=False, lazy="joined"
    )

    __mapper_args__ = {"polymorphic_on": kind}


class BuildVoteSession(VoteSession):
    """Association table between builds and vote sessions."""

    __tablename__ = "build_vote_sessions"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vote_sessions.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("builds.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    changes: Mapped[list[Any]] = mapped_column(JSON, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="build_vote_sessions", lazy="joined")
    build: Mapped[Build] = relationship(back_populates="build_vote_sessions", lazy="joined")

    __mapper_args__ = {"polymorphic_identity": "build"}


class DeleteLogVoteSession(VoteSession):
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

    vote_session: Mapped[VoteSession] = relationship(back_populates="delete_log_vote_sessions", lazy="joined")

    __mapper_args__ = {"polymorphic_identity": "delete_log"}


class Vote(Base):
    """A vote cast in a vote session."""

    __tablename__ = "votes"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vote_sessions.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    weight: Mapped[float] = mapped_column(Double)  # FIXME: Shouldn't be nullable

    vote_session: Mapped[VoteSession] = relationship(back_populates="votes", lazy="joined")


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
