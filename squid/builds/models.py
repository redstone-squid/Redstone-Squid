"""Stored builds and their category-specific details.

Build holds common fields, moderation status, and relationships such as creators,
media, and tags. Each category subclass adds its own table of details: a DoorBuild,
for example, has opening dimensions and timings alongside the common build row.
BuildService prepares these entities; the build repository saves their relationships.
"""

import typing
import uuid
from annotationlib import Format, get_annotations
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, field, fields
from typing import Any, Final, cast, get_args

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
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
    Status,
)
from squid.builds.domain.models import StagedMedia, StagedTaxonomy, freeze_fields, frozen_field
from squid.builds.errors import InvalidBuildError
from squid.config import EMBEDDING_DIMENSION
from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.messages.domain import MessageRef
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, IntEnumSmallInt, StrEnumText
from squid.sponsors import PublicSponsor
from squid.tags.models import TagAssignment


@freeze_fields
class Build(Base, StagedMedia, StagedTaxonomy, kw_only=True):
    """A build submitted by a user."""

    __tablename__ = "builds"
    __table_args__ = (
        Index("builds_submitter_idx", "submitter_account_id"),
        CheckConstraint("submission_status = ANY (ARRAY[0, 1, 2])", name="check_status"),
        CheckConstraint(
            "(locked_at IS NULL AND lock_token IS NULL AND lock_expires_at IS NULL) OR "
            "(locked_at IS NOT NULL AND lock_token IS NOT NULL AND lock_expires_at IS NOT NULL)",
            name="builds_lock_lease_complete",
        ),
        CheckConstraint("category IN ('Door','Extender','Utility','Entrance','Other')", name="builds_category_check"),
        CheckConstraint("revision > 0", name="builds_revision_positive"),
        CheckConstraint("depth > 0", name="submissions_build_depth_check"),
        CheckConstraint("height > 0", name="submissions_build_height_check"),
        CheckConstraint("width > 0", name="submissions_build_width_check"),
        CheckConstraint(
            "display_name IS NULL OR (display_name = btrim(display_name) AND display_name <> '' "
            "AND char_length(display_name) <= 120)",
            name="builds_display_name_valid",
        ),
        CheckConstraint(
            "sponsor_installation_id IS NOT NULL OR "
            "(sponsor_display_name IS NULL AND sponsor_address IS NULL AND sponsor_description IS NULL "
            "AND sponsor_website_url IS NULL)",
            name="builds_sponsor_projection_complete",
        ),
        CheckConstraint(
            "sponsor_display_name IS NULL OR char_length(sponsor_display_name) BETWEEN 1 AND 80",
            name="builds_sponsor_display_name_length",
        ),
        CheckConstraint(
            "sponsor_address IS NULL OR char_length(sponsor_address) BETWEEN 1 AND 255",
            name="builds_sponsor_address_length",
        ),
        CheckConstraint(
            "sponsor_description IS NULL OR char_length(sponsor_description) BETWEEN 1 AND 500",
            name="builds_sponsor_description_length",
        ),
        CheckConstraint(
            "sponsor_website_url IS NULL OR "
            "(char_length(sponsor_website_url) BETWEEN 1 AND 2048 AND sponsor_website_url ~ '^https?://')",
            name="builds_sponsor_website_valid",
        ),
        UniqueConstraint("source_submission_draft_id", name="builds_source_submission_draft_id_key"),
        Index("idx_builds_category", "category"),
        Index("idx_builds_submission_time", desc("submission_time")),
        Index(
            "builds_embedding_hnsw_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=None)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"), default=1)
    """Optimistic version checked by explicit repository writes."""
    submission_status: Mapped[Status | None] = mapped_column(IntEnumSmallInt(Status), nullable=False, default=None)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    """Declared width in blocks; None means unknown."""
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    depth: Mapped[int | None] = mapped_column(Integer, default=None)
    completion_time: Mapped[str | None] = mapped_column(Text, default=None)
    """Completion date as supplied by the submitter, including partial dates."""
    completion_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    completion_evidence: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    display_name: Mapped[str | None] = mapped_column(Text, default=None)
    source_submission_draft_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """Stable finalization key retained even if the short-lived source draft is later pruned."""
    sponsor_installation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None, compare=False)
    """Opaque Paper installation ID snapshotted without a mutable foreign key."""
    sponsor_display_name: Mapped[str | None] = mapped_column(Text, default=None, compare=False)
    sponsor_address: Mapped[str | None] = mapped_column(Text, default=None, compare=False)
    sponsor_description: Mapped[str | None] = mapped_column(Text, default=None, compare=False)
    sponsor_website_url: Mapped[str | None] = mapped_column(Text, default=None, compare=False)
    category: Mapped[BuildCategory] = mapped_column(
        StrEnumText(BuildCategory), nullable=False, init=False, compare=False
    )
    submitter_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="builds_submitter_account_id_fkey", ondelete="RESTRICT"),
        nullable=False,
        default=None,
    )
    version_spec: Mapped[str | None] = mapped_column(Text, default=None)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(EMBEDDING_DIMENSION), default=None)
    """Application-owned semantic vector stored in the authoritative build row."""
    locked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None, compare=False)
    lock_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None, compare=False)
    lock_expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None, compare=False)
    ai_generated: Mapped[bool | None] = mapped_column(Boolean, nullable=False, default=False)
    extra_info: Mapped[Info] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    submission_time: Mapped[Instant | None] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default=None
    )
    edited_time: Mapped[Instant | None] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default=None
    )

    # PostgreSQL deletes related rows through ON DELETE CASCADE. passive_deletes
    # avoids loading unloaded collections just to delete them. delete-orphan still
    # removes items dropped from a collection while the parent remains.
    build_creators: Mapped[list[BuildCreator]] = relationship(
        back_populates="build",
        default_factory=list,
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
        passive_deletes=True,
        compare=False,
    )
    build_versions: Mapped[list[BuildVersion]] = relationship(
        back_populates="build",
        default_factory=list,
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
        passive_deletes=True,
        compare=False,
    )
    tags: Mapped[list[TagAssignment]] = relationship(
        default_factory=list,
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    links: Mapped[list[BuildLink]] = relationship(  # type: ignore[bad-override]
        # SQLAlchemy exposes the same list as the media mixin through a descriptor.
        back_populates="build",
        default_factory=list,
        lazy="raise_on_sql",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __mapper_args__ = {
        "polymorphic_on": category,
        "version_id_col": revision,
    }

    versions: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    wiring_placement_restrictions: list[str] = field(default_factory=list)
    animated_restrictions: list[str] = field(default_factory=list)
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)
    creators_ign: list[str] = field(default_factory=list)
    sponsor: Final[PublicSponsor | None] = frozen_field(default=None)
    """Public sponsor details captured at submission time."""
    submitter_discord_id: int | None = None
    source_messages: Final[tuple[MessageRef, ...]] = frozen_field(default=())
    """Source messages in submission order. This provenance is fixed after submission.

    A loaded build holds plain references; read message content from the `messages` table.
    A new build may hold snapshots instead, which the repository records when it first saves the build.
    """

    def __post_init__(self) -> None:
        if type(self) is Build:
            raise InvalidStateError(
                tr("Build cannot be instantiated directly; construct a category subclass or a BuildDraft.")
            )

    @property
    def original_link(self) -> str | None:
        """Return the first source-message link with a known channel, or None.

        Source order prefers the initial submission over follow-up messages.
        A source with a known channel but no guild raises NotImplementedError.
        """
        for message in self.source_messages:
            if (link := message.link) is not None:
                return link
        return None

    @property
    def dimensions(self) -> tuple[int | None, int | None, int | None]:
        """Width, height, and depth in blocks; None means that dimension is unknown."""
        return self.width, self.height, self.depth

    @dimensions.setter
    def dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.width, self.height, self.depth = dimensions

    @property
    def title(self) -> str:
        """The display title with moderation markers and Markdown formatting."""
        from squid.builds.domain.titles import format_build_display_title

        return format_build_display_title(self, markdown=True)

    def edit_values(self) -> dict[str, Any]:
        """Return comparable fields, replacing related objects with their display data."""
        values = {item.name: getattr(self, item.name) for item in fields(self) if item.compare}
        values["links"] = [{"url": link.url, "media_type": link.media_type} for link in self.links]
        values["tags"] = [
            {
                "tag_id": tag.definition.id,
                "value": tag.value,
                "display_unit": tag.display_unit,
                "display_order": tag.display_order,
                "evidence": tag.evidence,
                "provenance": tag.provenance,
            }
            for tag in self.tags
        ]
        values["source_messages"] = [asdict(message) for message in self.source_messages]
        values["sponsor"] = None if self.sponsor is None else asdict(self.sponsor)
        return values

    def copy_for_edit(self, *, source_messages: tuple[MessageRef, ...] | None = None) -> Build:
        """Create an unsaved copy for previewing edits, without ORM history.

        Most values are deep-copied. Links and tag assignments are rebuilt, but
        assignments retain the original tag definitions.
        """
        values = {
            item.name: deepcopy(getattr(self, item.name))
            for item in fields(self)
            if item.init and item.compare and item.name not in {"links", "tags"}
        }
        values["links"] = [BuildLink(url=link.url, media_type=link.media_type) for link in self.links]
        values["tags"] = [
            TagAssignment(tag.definition, tag.value, tag.display_unit, tag.display_order, tag.evidence, tag.provenance)
            for tag in self.tags
        ]
        for original, copied in zip(self.tags, values["tags"], strict=True):
            copied.display_unit_definition = original.display_unit_definition
        if source_messages is not None:
            values["source_messages"] = source_messages
        return type(self)(**values)

    def diff[T: Any](self, other: Build, *, allow_different_id: bool = False) -> list[tuple[str, T, T]]:
        """Return changed fields as (name, old value, new value) tuples.

        Compare builds of the same category. Media changes are grouped by URL type.
        IDs are checked but are not reported as changes.

        Raises:
            InvalidBuildError: If categories differ, or IDs differ without
                allow_different_id=True.
        """
        if type(self) is not type(other):
            msg = "Cannot diff builds of different categories."
            raise InvalidBuildError(msg, context={"left": type(self).__name__, "right": type(other).__name__})
        if self.id != other.id and not allow_different_id:
            msg = "The IDs of the builds are different."
            raise InvalidBuildError(msg, context={"left_id": self.id, "right_id": other.id})

        differences: list[tuple[str, T, T]] = []
        mine = self.edit_values()
        theirs = other.edit_values()
        for name, value in mine.items():
            if name in {"id", "links"}:
                continue
            if value != theirs[name]:
                differences.append((name, value, theirs[name]))
        for media_type in get_args(MediaTypeLiteral):
            mine = list(self.urls_of(media_type))
            theirs = list(other.urls_of(media_type))
            if mine != theirs:
                differences.append((f"{media_type.replace('-', '_')}_urls", cast(T, mine), cast(T, theirs)))
        return differences

    def get_attr_type(self, attribute: str) -> type:
        """Return a field or property annotation, unwrapping SQLAlchemy Mapped."""
        cls = type(self)
        for owner in cls.__mro__:
            annotations = get_annotations(owner, format=Format.STRING)
            if attribute in annotations:
                annotation = get_annotations(owner)[attribute]
                return get_args(annotation)[0] if typing.get_origin(annotation) is Mapped else annotation
        cls_attr = getattr(cls, attribute, None)
        if isinstance(cls_attr, property) and cls_attr.fget is not None:
            return typing.get_type_hints(cls_attr.fget)["return"]
        msg = f"Attribute {attribute} is not on {cls.__name__}."
        raise InvalidBuildError(msg, context={"attribute": attribute})


class DoorBuild(Build, kw_only=True):
    """A door build with specific dimensions and timing information."""

    __tablename__ = "doors"
    __mapper_args__ = {
        "polymorphic_load": "selectin",
        "polymorphic_identity": BuildCategory.DOOR,
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="doors_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )
    orientation: Mapped[DoorOrientationLiteral] = mapped_column(Text, nullable=False, default="Door")
    door_width: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    door_height: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    door_depth: Mapped[int | None] = mapped_column(Integer, default=None)
    # Timings are game ticks; None means the timing has not been supplied.
    normal_opening_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    normal_closing_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    visible_opening_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    visible_closing_time: Mapped[int | None] = mapped_column(BigInteger, default=None)

    @property
    def door_dimensions(self) -> tuple[int, int, int | None]:
        """Width, height, and optional depth of the door opening, in blocks."""
        return self.door_width, self.door_height, self.door_depth


class ExtenderBuild(Build, kw_only=True):
    """An extender build."""

    __tablename__ = "extenders"
    __mapper_args__ = {
        "polymorphic_load": "selectin",
        "polymorphic_identity": BuildCategory.EXTENDER,
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="extenders_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )
    orientation: Mapped[str | None] = mapped_column(Text, default=None)
    extension_length: Mapped[int | None] = mapped_column(Integer, default=None)
    extender_type: Mapped[str | None] = mapped_column(Text, default=None)


class UtilityBuild(Build, kw_only=True):
    """A utility build."""

    __tablename__ = "utilities"
    __mapper_args__ = {
        "polymorphic_load": "selectin",
        "polymorphic_identity": BuildCategory.UTILITY,
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="utilities_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )


class EntranceBuild(Build, kw_only=True):
    """An entrance build."""

    __tablename__ = "entrances"
    __mapper_args__ = {
        "polymorphic_load": "selectin",
        "polymorphic_identity": BuildCategory.ENTRANCE,
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="entrances_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )


class OtherBuild(Build, kw_only=True):
    """A build which does not fit one of the structured catalogue categories."""

    __tablename__ = "other_builds"
    __mapper_args__ = {
        "polymorphic_load": "selectin",
        "polymorphic_identity": BuildCategory.OTHER,
    }

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="other_builds_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )


class BuildCreator(Base):
    """Association table between builds and the creator names credited on them."""

    __tablename__ = "build_creators"
    __table_args__ = (Index("build_creators_alias_idx", "alias_id"),)
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_creators_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )
    alias_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("creator_aliases.id", name="build_creators_alias_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
    )

    build: Mapped[Build] = relationship(back_populates="build_creators", lazy="raise_on_sql", repr=False, default=None)


# BuildLoader joins these links to messages in one batch query. A separate
# relationship on Build would duplicate that loading.
class BuildSourceMessage(Base):
    """Association table between builds and the Discord messages they came from.

    Many-to-many in both directions: one submission can span a body message plus
    follow-up images, and one build-log message can yield several builds at once.
    The message side is RESTRICT because a message row is a retained fact that
    outlives the builds referring to it; deleting a build only drops the link.
    """

    __tablename__ = "build_source_messages"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_source_messages_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        compare=False,
    )
    message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", name="build_source_messages_message_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    """Submission order, so the first message stays identifiable as the request itself."""


class BuildVersion(Base):
    """Association table between builds and their versions."""

    __tablename__ = "build_versions"
    __table_args__ = (Index("build_versions_version_idx", "version_id"),)
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_versions_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )
    version_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("versions.id", name="build_versions_version_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
    )

    build: Mapped[Build] = relationship(back_populates="build_versions", lazy="raise_on_sql", repr=False, default=None)


class BuildLink(Base):
    """A URL associated with a build, such as an image, schematic, or world download."""

    __tablename__ = "build_links"
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_links_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
        init=False,
        compare=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    media_type: Mapped[MediaTypeLiteral | None] = mapped_column(Text, default=None)  # TODO: nullable

    build: Mapped[Build] = relationship(back_populates="links", lazy="raise_on_sql", init=False, repr=False)


BUILD_CLASS_BY_CATEGORY: Mapping[BuildCategory, type[Build]] = {
    BuildCategory.DOOR: DoorBuild,
    BuildCategory.EXTENDER: ExtenderBuild,
    BuildCategory.UTILITY: UtilityBuild,
    BuildCategory.ENTRANCE: EntranceBuild,
    BuildCategory.OTHER: OtherBuild,
}
