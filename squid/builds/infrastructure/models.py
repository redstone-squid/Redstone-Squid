"""SQLAlchemy build and taxonomy models."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from whenever import Instant

from squid.builds.domain import (
    BuildCategory,
    DoorOrientationLiteral,
    Info,
    MediaTypeLiteral,
    RecordCategoryLiteral,
    Status,
)
from squid.config import EMBEDDING_DIMENSION
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC

if TYPE_CHECKING:
    from squid.tags.infrastructure.models import BuildTagAssignment


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
        CheckConstraint("revision > 0", name="builds_revision_positive"),
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
        Index(
            "builds_embedding_hnsw_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"), default=1)
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
    submitter_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="builds_submitter_account_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
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
        VECTOR(EMBEDDING_DIMENSION),
        comment="Application-owned semantic vector stored in the authoritative build row.",
        default=None,
    )
    locked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    lock_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    lock_expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
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

    __mapper_args__ = {"version_id_col": revision}

    build_creators: Mapped[list[BuildCreator]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )
    build_versions: Mapped[list[BuildVersion]] = relationship(
        back_populates="build", default_factory=list, lazy="selectin"
    )

    tag_assignments: Mapped[list[BuildTagAssignment]] = relationship(
        default_factory=list,
        lazy="selectin",
        cascade="all, delete-orphan",
    )

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
    """Association table between builds and the creator names credited on them."""

    __tablename__ = "build_creators"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_creators_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
    )
    alias_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("creator_aliases.id", name="build_creators_alias_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
    )

    build: Mapped[Build] = relationship(back_populates="build_creators", lazy="raise_on_sql", repr=False, default=None)


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
