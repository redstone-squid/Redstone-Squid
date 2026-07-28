import inspect
import os
import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal, TypeAlias, TypedDict, cast, get_args

from advanced_alchemy.base import BasicAttributes
from pgvector.sqlalchemy import VECTOR
from pydantic.types import Json
from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column, relationship
from sqlalchemy.sql import func

from squid.db._docs_extraction import extract_attribute_docstrings

RecordCategoryLiteral: TypeAlias = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Sequence[RecordCategoryLiteral] = cast(
    Sequence[RecordCategoryLiteral], get_args(RecordCategoryLiteral)
)

BuildCategoryLiteral: TypeAlias = Literal["Door", "Extender", "Utility", "Entrance"]
BUILD_TYPES: Sequence[BuildCategoryLiteral] = cast(Sequence[BuildCategoryLiteral], get_args(BuildCategoryLiteral))

DoorOrientationLiteral: TypeAlias = Literal["Door", "Skydoor", "Trapdoor"]
DOOR_ORIENTATION_NAMES = cast(Sequence[DoorOrientationLiteral], get_args(DoorOrientationLiteral))

RestrictionTypeLiteral = Literal["wiring-placement", "component", "miscellaneous"]
RESTRICTIONS = cast(Sequence[RestrictionTypeLiteral], get_args(RestrictionTypeLiteral))

MessagePurposeLiteral = Literal["view_pending_build", "view_confirmed_build", "vote", "build_original_message"]

VoteKindLiteral = Literal["build", "delete_log"]
VoteSessionResultLiteral: TypeAlias = Literal["approved", "denied", "cancelled", "pending"]
VoteChoiceLiteral: TypeAlias = Literal["approve", "deny"]

MediaTypeLiteral = Literal["image", "video", "world-download"]

ScalarChannelSetting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
ListRoleSetting = Literal["Staff", "Trusted"]
Setting = Literal["Smallest", "Fastest", "First", "Builds", "Vote", "Staff", "Trusted"]


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


class BuildCategory(StrEnum):
    """The categories of the builds."""

    DOOR = "Door"
    EXTENDER = "Extender"
    UTILITY = "Utility"
    ENTRANCE = "Entrance"


# AIDEV-NOTE: SQLAlchemy table definitions for gradual migration from Supabase
class Base(BasicAttributes, AsyncAttrs, MappedAsDataclass, DeclarativeBase):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Populate table/column comments from docstrings, so the database is self-documenting.

        A class docstring becomes the table comment; a bare string literal
        immediately following an attribute's annotation becomes that column's
        comment (mirroring how attribute docstrings are written throughout
        this module, e.g. in pydantic models).
        """
        is_mapped_table = "__tablename__" in cls.__dict__

        # Table construction happens inside DeclarativeBase's __init_subclass__, so
        # __table_args__ must be finalized before we delegate to it via super().
        if is_mapped_table and cls.__doc__ is not None:
            table_comment = inspect.cleandoc(cls.__doc__)
            if not hasattr(cls, "__table_args__"):
                cls.__table_args__ = {"comment": table_comment}
            elif isinstance(cls.__table_args__, dict) and cls.__table_args__.get("comment") is None:
                cls.__table_args__["comment"] = table_comment
            elif isinstance(cls.__table_args__, tuple):
                if cls.__table_args__ and isinstance(cls.__table_args__[-1], dict):
                    cls.__table_args__[-1].setdefault("comment", table_comment)
                else:
                    cls.__table_args__ = (*cls.__table_args__, {"comment": table_comment})

        super().__init_subclass__(**kwargs)

        if not is_mapped_table:
            return  # Mixin or abstract base, not a mapped table.

        # Columns only exist as mapped attributes after the delegation above.
        for attribute, comment in extract_attribute_docstrings(cls).items():
            column = getattr(cls, attribute, None)
            underlying_column = getattr(column, "column", None)
            if underlying_column is not None and underlying_column.comment is None:
                underlying_column.comment = comment


class User(Base):
    """A user in the system, which can be linked to both Discord and Minecraft accounts."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    """Internal primary key. Unrelated to the user's Discord or Minecraft identifiers."""
    ign: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    """The user's Minecraft in-game name, as of the last verification."""
    discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    """The user's Discord snowflake ID, if they have linked a Discord account."""
    minecraft_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """The user's Mojang account UUID, if they have linked a Minecraft account."""
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.now(), default=None
    )
    """When this row was first inserted."""

    build_creators: Mapped[list["BuildCreator"]] = relationship(
        back_populates="user", default_factory=list, lazy="raise_on_sql", repr=False
    )
    builds: AssociationProxy[list["Build"]] = association_proxy(
        "build_creators", "build", default_factory=list, repr=False, creator=lambda b: BuildCreator(build=b)
    )


class Version(Base):
    """A version of Minecraft that a build is compatible with."""

    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    edition: Mapped[str] = mapped_column(Text, nullable=False)
    major_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minor_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patch_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    build_versions: Mapped[list["BuildVersion"]] = relationship(
        back_populates="version", default_factory=list, lazy="raise_on_sql", repr=False
    )
    builds: AssociationProxy[list["Build"]] = association_proxy(
        "build_versions",
        "build",
        default_factory=list,
        repr=False,
        creator=lambda b: BuildVersion(build=b),
    )


class Restriction(Base):
    """A restriction that can be applied to builds."""

    __tablename__ = "restrictions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    build_category: Mapped[BuildCategoryLiteral | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(
        Text, nullable=True, unique=True
    )  # FIXME: Shouldn't be nullable, note that to make type checkers happy I made this Mapped[str] instead of Mapped[str | None], even though it is nullable in the database
    type: Mapped[RestrictionTypeLiteral | None] = mapped_column(Text)

    build_restrictions: Mapped[list["BuildRestriction"]] = relationship(
        back_populates="restriction", default_factory=list, lazy="raise_on_sql", repr=False
    )
    builds: AssociationProxy[list["Build"]] = association_proxy(
        "build_restrictions",
        "build",
        default_factory=list,
        repr=False,
        creator=lambda b: BuildRestriction(build=b),
    )

    aliases: Mapped[list["RestrictionAlias"]] = relationship(
        back_populates="restriction", default_factory=list, lazy="selectin"
    )


class RestrictionAlias(Base):
    """An alias for a restriction, allowing for alternative names."""

    __tablename__ = "restriction_aliases"
    restriction_id: Mapped[int] = mapped_column(
        SmallInteger,
        Identity(),
        ForeignKey(
            "restrictions.id",
            name="restriction_aliases_restriction_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )

    __table_args__ = (Index("restriction_aliases_restriction_id_idx", "restriction_id"),)

    restriction: Mapped[Restriction] = relationship(back_populates="aliases", init=False, lazy="joined")


class Type(Base):
    """A build pattern."""

    __tablename__ = "types"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    build_category: Mapped[BuildCategoryLiteral | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(
        Text, nullable=True, unique=True
    )  # FIXME: This should be unique per build category  # FIXME: shouldn't be nullable

    build_types: Mapped[list["BuildType"]] = relationship(
        back_populates="type", default_factory=list, lazy="raise_on_sql", repr=False
    )
    builds: AssociationProxy[list["Build"]] = association_proxy(
        "build_types", "build", default_factory=list, creator=lambda b: BuildType(build=b), repr=False
    )


class Message(Base):
    """A message associated with a build or vote session."""

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True
    )  # init=True because this is the message ID, which should be known when creating the object
    server_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("server_settings.server_id", name="public_messages_server_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str] = mapped_column(
        Text, nullable=False, comment="The reason why the message is stored in the database"
    )
    content: Mapped[str | None] = mapped_column(Text)
    build_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="public_messages_build_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    vote_session_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("vote_sessions.id", name="messages_vote_session_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=func.now(), onupdate=func.now()
    )
    """When this row was last modified. Bumped automatically on every UPDATE."""

    build: Mapped["Build | None"] = relationship(
        back_populates="messages", foreign_keys="Message.build_id", default=None, lazy="raise_on_sql"
    )
    vote_session: Mapped["VoteSession | None"] = relationship(
        back_populates="messages", default=None, lazy="raise_on_sql"
    )


class Build(Base, kw_only=True):
    """A build submitted by a user."""

    __tablename__ = "builds"
    __table_args__ = (
        CheckConstraint(
            "record_category = ANY (ARRAY['Smallest', 'Fastest', 'First', 'Smallest Fastest', "
            "'Fastest Smallest', NULL])",
            name="check_record_category",
        ),
        CheckConstraint("submission_status = ANY (ARRAY[0, 1, 2])", name="check_status"),
        CheckConstraint("depth > 0", name="submissions_build_depth_check"),
        CheckConstraint("height > 0", name="submissions_build_height_check"),
        CheckConstraint("width > 0", name="submissions_build_width_check"),
        Index("idx_builds_category", "category", postgresql_where=text("category IS NOT NULL")),
        Index(
            "idx_builds_record_category",
            "record_category",
            postgresql_where=text("record_category IS NOT NULL"),
        ),
        Index("idx_builds_submission_time", desc("submission_time")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    submission_status: Mapped["Status"] = mapped_column(SmallInteger, nullable=False)
    record_category: Mapped[RecordCategoryLiteral | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    depth: Mapped[int | None] = mapped_column(Integer)
    completion_time: Mapped[str | None] = mapped_column(Text)  # Given by user, not parsable as a datetime
    category: Mapped["BuildCategory | None"] = mapped_column(Text)
    submitter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "messages.id",
            name="builds_original_message_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
            use_alter=True,
        ),
        default=None,
    )
    original_message: Mapped[Message | None] = relationship(
        foreign_keys="Build.original_message_id", uselist=False, default=None, lazy="joined"
    )
    version_spec: Mapped[str | None] = mapped_column(Text, default=None)
    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(int(os.getenv("EMBEDDING_DIMENSION", "1536"))),
        comment='This is not actually being used. See "vecs"."builds" instead',
        default=None,
    )
    locked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_info: Mapped[Info] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    submission_time: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.now(), default=func.now()
    )
    edited_time: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("(now() AT TIME ZONE 'utc'::text)"), default=func.now()
    )
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)

    build_creators: Mapped[list["BuildCreator"]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )
    creators: AssociationProxy[list[User]] = association_proxy(
        "build_creators", "user", default_factory=list, creator=lambda u: BuildCreator(user=u)
    )

    build_restrictions: Mapped[list["BuildRestriction"]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )
    restrictions: AssociationProxy[list[Restriction]] = association_proxy(
        "build_restrictions",
        "restriction",
        default_factory=list,
        creator=lambda r: BuildRestriction(restriction=r),
    )

    build_versions: Mapped[list["BuildVersion"]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )
    versions: AssociationProxy[list[Version]] = association_proxy(
        "build_versions", "version", default_factory=list, creator=lambda v: BuildVersion(version=v)
    )

    build_types: Mapped[list["BuildType"]] = relationship(back_populates="build", default_factory=list, lazy="selectin")
    types: AssociationProxy[list[Type]] = association_proxy(
        "build_types", "type", default_factory=list, creator=lambda t: BuildType(type=t)
    )

    build_vote_sessions: Mapped[list["BuildVoteSession"]] = relationship(
        back_populates="build", default_factory=list, lazy="raise_on_sql", repr=False
    )

    links: Mapped[list["BuildLink"]] = relationship(back_populates="build", default_factory=list, lazy="selectin")
    messages: Mapped[list[Message]] = relationship(
        back_populates="build", foreign_keys="Message.build_id", default_factory=list, lazy="raise_on_sql", repr=False
    )

    __mapper_args__ = {
        "polymorphic_on": category,
    }


class Door(Build, kw_only=True):
    """A door build with specific dimensions and timing information."""

    __tablename__ = "doors"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Door",
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="doors_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )
    orientation: Mapped[DoorOrientationLiteral] = mapped_column(Text, nullable=False)
    door_width: Mapped[int] = mapped_column(Integer, nullable=False)
    door_height: Mapped[int] = mapped_column(Integer, nullable=False)
    door_depth: Mapped[int | None] = mapped_column(Integer)
    normal_opening_time: Mapped[int | None] = mapped_column(BigInteger)
    normal_closing_time: Mapped[int | None] = mapped_column(BigInteger)
    visible_opening_time: Mapped[int | None] = mapped_column(BigInteger)
    visible_closing_time: Mapped[int | None] = mapped_column(BigInteger)


class SmallestDoor(Base):
    """A door that is the smallest in a specific category.

    This table is a cache for the smallest doors in each category up to 8 restrictions, built by using database triggers
    """

    __tablename__ = "smallest_door_records"
    __table_args__ = (
        UniqueConstraint(
            "orientation",
            "door_width",
            "door_height",
            "door_depth",
            "types",
            "restriction_subset",
            name="smallest_door_records_orientation_door_width_door_height_do_key",
        ),
        Index(
            "idx_smallest_door_records_dims",
            "orientation",
            "door_width",
            "door_height",
            "door_depth",
        ),
        Index("idx_smallest_door_records_restrictions_gin", "restrictions", postgresql_using="gin"),
        Index("idx_smallest_door_records_types_gin", "types", postgresql_using="gin"),
        Index(
            "unq_smallest_key",
            "orientation",
            "door_width",
            "door_height",
            "door_depth",
            "types",
            "restriction_subset",
            unique=True,
        ),
    )

    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="smallest_door_records_id_fkey", ondelete="CASCADE"),
        init=False,
    )
    door_width: Mapped[int] = mapped_column(Integer, nullable=False)
    door_height: Mapped[int] = mapped_column(Integer, nullable=False)
    door_depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    orientation: Mapped[DoorOrientationLiteral] = mapped_column(Text, nullable=False)
    types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    restrictions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    restriction_subset: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)


class Extender(Build, kw_only=True):
    """An extender build."""

    __tablename__ = "extenders"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Extender",
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="extenders_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )


class Utility(Build, kw_only=True):
    """A utility build."""

    __tablename__ = "utilities"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Utility",
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="utilities_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )


class Entrance(Build):
    """An entrance build."""

    __tablename__ = "entrances"
    __mapper_args__ = {
        "polymorphic_load": "inline",
        "polymorphic_identity": "Entrance",
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="entrances_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )


class BuildCreator(Base):
    """Association table between builds and their creators."""

    __tablename__ = "build_creators"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_creators_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="build_creators_user_id_fkey"),
        primary_key=True,
        init=False,
    )

    build: Mapped[Build] = relationship(back_populates="build_creators", lazy="raise_on_sql", repr=False, default=None)
    user: Mapped[User] = relationship(back_populates="build_creators", lazy="joined", repr=False, default=None)


class BuildRestriction(Base):
    """Association table between builds and their restrictions."""

    __tablename__ = "build_restrictions"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_restrictions_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )
    restriction_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("restrictions.id", name="build_restrictions_restriction_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
        init=False,
    )

    build: Mapped[Build] = relationship(
        back_populates="build_restrictions", lazy="raise_on_sql", repr=False, default=None
    )
    restriction: Mapped[Restriction] = relationship(
        back_populates="build_restrictions", lazy="joined", repr=False, default=None
    )


class BuildVersion(Base):
    """Association table between builds and their versions."""

    __tablename__ = "build_versions"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_versions_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )
    version_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("versions.id", name="build_versions_version_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
        init=False,
    )

    build: Mapped[Build] = relationship(back_populates="build_versions", lazy="raise_on_sql", repr=False, default=None)
    version: Mapped[Version] = relationship(back_populates="build_versions", lazy="joined", repr=False, default=None)


class BuildType(Base):
    """Association table between builds and their types."""

    __tablename__ = "build_types"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_types_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )
    type_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("types.id", name="build_types_type_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
        init=False,
    )

    build: Mapped[Build] = relationship(back_populates="build_types", lazy="raise_on_sql", repr=False, default=None)
    type: Mapped[Type] = relationship(back_populates="build_types", lazy="joined", repr=False, default=None)


class BuildLink(Base):
    """A link associated with a build (image, video, world download)."""

    __tablename__ = "build_links"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_links_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    media_type: Mapped[MediaTypeLiteral | None] = mapped_column(Text)  # TODO: nullable

    build: Mapped[Build] = relationship(back_populates="links", lazy="raise_on_sql", init=False, repr=False)


class BuildEditHistory(Base):
    """A version marker recorded when a build is edited."""

    __tablename__ = "build_edit_history"
    __table_args__ = (UniqueConstraint("build_id", "version", name="unique_version_per_build"),)

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        ForeignKey(
            "builds.id",
            name="build_edit_history_build_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
        init=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), default=func.now(), init=False
    )
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class ServerSetting(Base):
    """Settings for a Discord server."""

    __tablename__ = "server_settings"
    server_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    smallest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    fastest_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    first_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    builds_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    voting_channel_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    staff_roles_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=True, default_factory=list)
    trusted_roles_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=True, default_factory=list)
    in_server: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)


class VerificationCode(Base):
    """A verification code for linking Minecraft accounts."""

    __tablename__ = "verification_codes"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    minecraft_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    created: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=func.now(), default=func.now()
    )
    expires: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("(now() + '00:10:00'::interval)"),
        default=func.now() + text("INTERVAL '10 minutes'"),
    )


class VoteSession(Base, kw_only=True):
    """A voting session for builds or log deletions."""

    __tablename__ = "vote_sessions"
    __table_args__ = (
        CheckConstraint("fail_threshold < 0", name="vote_sessions_fail_threshold_check"),
        CheckConstraint("pass_threshold > 0", name="vote_sessions_pass_threshold_check"),
        CheckConstraint(
            "result = ANY (ARRAY['approved', 'denied', 'cancelled', 'pending'])",
            name="vote_sessions_result_check",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[VoteSessionResultLiteral] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'::text"),
        comment="The result of the vote session.",
    )
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    pass_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fail_threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="vote_session", default_factory=list, lazy="selectin", init=False, repr=False
    )
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="vote_session", default_factory=list, lazy="selectin", init=False, repr=False
    )
    options: Mapped[list["VoteSessionOption"]] = relationship(
        back_populates="vote_session",
        default_factory=list,
        lazy="selectin",
        order_by="VoteSessionOption.position",
        init=False,
        repr=False,
    )

    __mapper_args__ = {"polymorphic_on": kind}


class VoteSessionOption(Base, kw_only=True):
    """A reaction option configured for one vote session."""

    __tablename__ = "vote_session_options"
    __table_args__ = (
        UniqueConstraint("vote_session_id", "position", name="vote_session_options_vote_session_id_position_key"),
        CheckConstraint("choice IN ('approve', 'deny')", name="vote_session_options_choice_check"),
        CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="vote_session_options_multiplier_check",
        ),
        CheckConstraint("position >= 0", name="vote_session_options_position_check"),
        {"comment": "Ordered reaction options and positive weight multipliers captured for each vote session."},
    )

    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "vote_sessions.id",
            name="vote_session_options_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    emoji: Mapped[str] = mapped_column(Text, primary_key=True)
    choice: Mapped[VoteChoiceLiteral] = mapped_column(Text, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"), default=1.0)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    vote_session: Mapped[VoteSession] = relationship(back_populates="options", lazy="raise_on_sql", repr=False)


class BuildVoteSession(VoteSession, kw_only=True):
    """Association table between builds and vote sessions."""

    __tablename__ = "build_vote_sessions"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        ForeignKey(
            "vote_sessions.id",
            name="build_vote_sessions_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "builds.id",
            name="build_vote_sessions_build_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    changes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    build: Mapped[Build] = relationship(back_populates="build_vote_sessions", lazy="joined")

    __mapper_args__ = {"polymorphic_identity": "build"}


class DeleteLogVoteSession(VoteSession, kw_only=True):
    """Association table between vote sessions and messages to be deleted."""

    __tablename__ = "delete_log_vote_sessions"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        ForeignKey(
            "vote_sessions.id",
            name="delete_log_vote_sessions_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
        primary_key=True,
    )
    target_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __mapper_args__ = {"polymorphic_identity": "delete_log"}


class Vote(Base):
    """A vote cast in a vote session."""

    __tablename__ = "votes"
    vote_session_id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        ForeignKey(
            "vote_sessions.id",
            name="votes_vote_session_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    weight: Mapped[float] = mapped_column(Float, nullable=True)  # FIXME: Shouldn't be nullable

    vote_session: Mapped[VoteSession] = relationship(back_populates="votes", lazy="raise_on_sql", repr=False)


class BuildRecord(TypedDict):
    """A record of a build in the database."""

    id: int
    submission_status: Status
    record_category: RecordCategoryLiteral | None
    extra_info: Info
    submission_time: str
    edited_time: str
    width: int | None
    height: int | None
    depth: int | None
    completion_time: str | None  # Given by user, not parsable as a datetime
    category: BuildCategory
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
    purpose: MessagePurposeLiteral
    build_id: int | None
    vote_session_id: int | None
    content: str | None


class DoorRecord(TypedDict):
    """A record of a door in the database."""

    build_id: int
    orientation: DoorOrientationLiteral
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
    build_category: BuildCategory
    name: str


class RestrictionRecord(TypedDict):
    """A restriction on a build."""

    id: int
    build_category: BuildCategory
    name: str
    type: RestrictionTypeLiteral


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
    result: VoteSessionResultLiteral
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
