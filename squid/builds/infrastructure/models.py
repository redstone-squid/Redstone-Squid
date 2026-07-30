"""SQLAlchemy build and taxonomy models."""

from __future__ import annotations

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from whenever import Instant

from squid.builds.domain import (
    BuildCategory,
    BuildCategoryLiteral,
    DoorOrientationLiteral,
    Info,
    MediaTypeLiteral,
    RecordCategoryLiteral,
    RestrictionTypeLiteral,
    Status,
)
from squid.config import embedding_dimension_from_environment
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class Restriction(Base):
    """A restriction that can be applied to builds."""

    __tablename__ = "restrictions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    build_category: Mapped[BuildCategoryLiteral | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(
        Text, nullable=True, unique=True
    )  # FIXME: Shouldn't be nullable, note that to make type checkers happy I made this Mapped[str] instead of Mapped[str | None], even though it is nullable in the database
    type: Mapped[RestrictionTypeLiteral | None] = mapped_column(Text)

    build_restrictions: Mapped[list[BuildRestriction]] = relationship(
        back_populates="restriction", default_factory=list, lazy="raise_on_sql", repr=False
    )
    aliases: Mapped[list[RestrictionAlias]] = relationship(
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
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
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

    build_types: Mapped[list[BuildType]] = relationship(
        back_populates="type", default_factory=list, lazy="raise_on_sql", repr=False
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
    submission_status: Mapped[Status] = mapped_column(SmallInteger, nullable=False)
    record_category: Mapped[RecordCategoryLiteral | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    depth: Mapped[int | None] = mapped_column(Integer)
    completion_time: Mapped[str | None] = mapped_column(Text)  # Given by user, not parsable as a datetime
    completion_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    completion_evidence: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    category: Mapped[BuildCategory | None] = mapped_column(Text)
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
    version_spec: Mapped[str | None] = mapped_column(Text, default=None)
    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(embedding_dimension_from_environment()),
        comment='This is not actually being used. See "vecs"."builds" instead',
        default=None,
    )
    locked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_info: Mapped[Info] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    submission_time: Mapped[Instant | None] = mapped_column(
        InstantUTC(), server_default=func.now(), default_factory=Instant.now
    )
    edited_time: Mapped[Instant | None] = mapped_column(
        InstantUTC(), server_default=func.now(), default_factory=Instant.now
    )
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)

    build_creators: Mapped[list[BuildCreator]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )
    build_restrictions: Mapped[list[BuildRestriction]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )

    build_versions: Mapped[list[BuildVersion]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )

    build_types: Mapped[list[BuildType]] = relationship(back_populates="build", default_factory=list, lazy="selectin")

    links: Mapped[list[BuildLink]] = relationship(back_populates="build", default_factory=list, lazy="selectin")

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
    orientation: Mapped[str | None] = mapped_column(Text, default=None)
    extension_length: Mapped[int | None] = mapped_column(Integer, default=None)
    extender_type: Mapped[str | None] = mapped_column(Text, default=None)


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
    )

    build: Mapped[Build] = relationship(back_populates="build_creators", lazy="raise_on_sql", repr=False, default=None)


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
    )

    build: Mapped[Build] = relationship(back_populates="build_versions", lazy="raise_on_sql", repr=False, default=None)


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
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now, init=False
    )
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
