"""SQLAlchemy models for unified build tags."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC
from squid.tags.domain import (
    RecordOperator,
    TagAuthority,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)


class UnitDefinition(Base, kw_only=True):
    """A unit accepted by numeric tag inputs."""

    __tablename__ = "tag_units"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default_factory=list)
    scale_to_base: Mapped[Decimal] = mapped_column(Numeric, nullable=False)


class TagDefinition(Base, kw_only=True):
    """A canonical tag that may be assigned to builds."""

    __tablename__ = "tag_definitions"
    __table_args__ = (
        UniqueConstraint("id", "value_type", name="tag_definitions_id_value_type_key"),
        UniqueConstraint("stable_key", name="tag_definitions_stable_key_key"),
        UniqueConstraint("query_name", name="tag_definitions_query_name_key"),
        CheckConstraint(
            "authority IN ('official', 'user')",
            name="tag_definitions_authority_check",
        ),
        CheckConstraint(
            "semantic_kind IN ('restriction', 'pattern', 'showcase')",
            name="tag_definitions_semantic_kind_check",
        ),
        CheckConstraint(
            "value_type IN ('none', 'numeric', 'text', 'boolean')",
            name="tag_definitions_value_type_check",
        ),
        CheckConstraint(
            "moderation_status IN ('pending', 'approved', 'rejected', 'archived')",
            name="tag_definitions_moderation_status_check",
        ),
        CheckConstraint(
            "record_operator IS NULL OR record_operator IN ('present', 'exact', 'at_most', 'at_least')",
            name="tag_definitions_record_operator_check",
        ),
        CheckConstraint(
            "query_name IS NULL OR query_name ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="tag_definitions_query_name_format_check",
        ),
        CheckConstraint(
            "(authority = 'official') OR "
            "(semantic_kind = 'showcase' AND restriction_type IS NULL AND record_operator IS NULL)",
            name="tag_definitions_user_showcase_only_check",
        ),
        CheckConstraint(
            "(semantic_kind = 'restriction' AND restriction_type IS NOT NULL) OR "
            "(semantic_kind <> 'restriction' AND restriction_type IS NULL)",
            name="tag_definitions_restriction_type_check",
        ),
        CheckConstraint(
            "(value_type = 'numeric') = (canonical_unit_key IS NOT NULL OR numeric_quantum IS NOT NULL) OR "
            "(value_type = 'numeric' AND canonical_unit_key IS NULL AND numeric_quantum IS NULL)",
            name="tag_definitions_numeric_metadata_check",
        ),
        CheckConstraint(
            "value_type = 'numeric' OR "
            "(canonical_unit_key IS NULL AND default_display_unit_key IS NULL AND numeric_quantum IS NULL)",
            name="tag_definitions_non_numeric_unit_check",
        ),
        CheckConstraint(
            "numeric_quantum IS NULL OR numeric_quantum > 0",
            name="tag_definitions_numeric_quantum_check",
        ),
        CheckConstraint(
            "(record_operator = 'present' AND value_type = 'none') OR "
            "(record_operator IN ('at_most', 'at_least') AND value_type = 'numeric') OR "
            "(record_operator = 'exact' AND value_type <> 'none') OR record_operator IS NULL",
            name="tag_definitions_record_operator_value_check",
        ),
        CheckConstraint("default_display_order >= 0", name="tag_definitions_display_order_check"),
        Index("tag_definitions_lookup_idx", "normalized_name", "semantic_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    query_name: Mapped[str | None] = mapped_column(Text, default=None)
    authority: Mapped[TagAuthority] = mapped_column(Text, nullable=False)
    semantic_kind: Mapped[TagSemanticKind] = mapped_column(Text, nullable=False)
    restriction_type: Mapped[str | None] = mapped_column(Text, default=None)
    value_type: Mapped[TagValueType] = mapped_column(Text, nullable=False)
    record_operator: Mapped[RecordOperator | None] = mapped_column(Text, default=None)
    canonical_unit_key: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("tag_units.key", name="tag_definitions_canonical_unit_fkey", ondelete="RESTRICT"),
        default=None,
    )
    default_display_unit_key: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("tag_units.key", name="tag_definitions_display_unit_fkey", ondelete="RESTRICT"),
        default=None,
    )
    numeric_quantum: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    render_template: Mapped[str] = mapped_column(Text, nullable=False, default="{name}")
    default_display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    moderation_status: Mapped[TagModerationStatus] = mapped_column(Text, nullable=False)
    created_by_discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    archived_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)

    aliases: Mapped[list[TagAlias]] = relationship(back_populates="definition", default_factory=list, lazy="selectin")
    applicabilities: Mapped[list[TagApplicability]] = relationship(
        back_populates="definition", default_factory=list, lazy="selectin"
    )
    assignments: Mapped[list[BuildTagAssignment]] = relationship(
        back_populates="definition", default_factory=list, lazy="raise_on_sql", repr=False
    )


class TagAlias(Base, kw_only=True):
    """An alternate display name for a tag."""

    __tablename__ = "tag_aliases"
    __table_args__ = (
        PrimaryKeyConstraint("tag_id", "normalized_alias"),
        Index("tag_aliases_normalized_idx", "normalized_alias"),
    )

    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tag_definitions.id", name="tag_aliases_tag_id_fkey", ondelete="CASCADE"),
        init=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    definition: Mapped[TagDefinition] = relationship(back_populates="aliases", lazy="joined", default=None)


class TagApplicability(Base, kw_only=True):
    """A build kind on which a tag may be used."""

    __tablename__ = "tag_applicabilities"
    __table_args__ = (PrimaryKeyConstraint("tag_id", "build_kind"),)

    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tag_definitions.id", name="tag_applicabilities_tag_id_fkey", ondelete="CASCADE"),
        init=False,
    )
    build_kind: Mapped[str] = mapped_column(Text, nullable=False)

    definition: Mapped[TagDefinition] = relationship(back_populates="applicabilities", lazy="joined", default=None)


class BuildTagAssignment(Base, kw_only=True):
    """A typed tag value attached to one build."""

    __tablename__ = "build_tag_assignments"
    __table_args__ = (
        PrimaryKeyConstraint("build_id", "tag_id"),
        ForeignKeyConstraint(
            ["tag_id", "value_type"],
            ["tag_definitions.id", "tag_definitions.value_type"],
            name="build_tag_assignments_definition_value_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(value_type = 'none' AND num_nonnulls(numeric_value, text_value, boolean_value) = 0) OR "
            "(value_type = 'numeric' AND numeric_value IS NOT NULL "
            "AND num_nonnulls(text_value, boolean_value) = 0) OR "
            "(value_type = 'text' AND text_value IS NOT NULL "
            "AND num_nonnulls(numeric_value, boolean_value) = 0) OR "
            "(value_type = 'boolean' AND boolean_value IS NOT NULL "
            "AND num_nonnulls(numeric_value, text_value) = 0)",
            name="build_tag_assignments_typed_value_check",
        ),
        CheckConstraint(
            "numeric_value IS NULL OR numeric_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="build_tag_assignments_finite_numeric_check",
        ),
        CheckConstraint("display_order IS NULL OR display_order >= 0", name="build_tag_assignments_order_check"),
        CheckConstraint(
            "provenance IN ('submitted', 'inferred', 'moderated', 'legacy_import')",
            name="build_tag_assignments_provenance_check",
        ),
        Index("build_tag_assignments_tag_build_idx", "tag_id", "build_id"),
        Index(
            "build_tag_assignments_numeric_idx",
            "tag_id",
            "numeric_value",
            "build_id",
            postgresql_where=text("numeric_value IS NOT NULL"),
        ),
        Index(
            "build_tag_assignments_text_idx",
            "tag_id",
            "text_value",
            "build_id",
            postgresql_where=text("text_value IS NOT NULL"),
        ),
    )

    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="build_tag_assignments_build_id_fkey", ondelete="CASCADE"),
        init=False,
    )
    tag_id: Mapped[int] = mapped_column(BigInteger, init=False)
    value_type: Mapped[TagValueType] = mapped_column(Text, nullable=False)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    text_value: Mapped[str | None] = mapped_column(Text, default=None)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, default=None)
    display_unit_key: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("tag_units.key", name="build_tag_assignments_display_unit_fkey", ondelete="RESTRICT"),
        default=None,
    )
    display_order: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    evidence: Mapped[str | None] = mapped_column(Text, default=None)
    provenance: Mapped[str] = mapped_column(Text, nullable=False, default="submitted")
    created_by_discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    definition: Mapped[TagDefinition] = relationship(back_populates="assignments", lazy="joined", default=None)


class TagRelation(Base, kw_only=True):
    """A semantic relationship between official restrictions."""

    __tablename__ = "tag_relations"
    __table_args__ = (
        PrimaryKeyConstraint("source_tag_id", "relation_kind", "target_tag_id"),
        CheckConstraint("relation_kind IN ('implies', 'incompatible')", name="tag_relations_kind_check"),
        CheckConstraint("source_tag_id <> target_tag_id", name="tag_relations_distinct_check"),
    )

    source_tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tag_definitions.id", name="tag_relations_source_fkey", ondelete="CASCADE"),
    )
    relation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tag_definitions.id", name="tag_relations_target_fkey", ondelete="CASCADE"),
    )


class TagRecordThreshold(Base, kw_only=True):
    """A staff-seeded eager threshold for a parameterized restriction."""

    __tablename__ = "tag_record_thresholds"
    __table_args__ = (
        PrimaryKeyConstraint("tag_id", "numeric_value"),
        ForeignKeyConstraint(
            ["tag_id", "value_type"],
            ["tag_definitions.id", "tag_definitions.value_type"],
            name="tag_record_thresholds_definition_value_fkey",
            ondelete="CASCADE",
        ),
        CheckConstraint("value_type = 'numeric'", name="tag_record_thresholds_numeric_check"),
    )

    tag_id: Mapped[int] = mapped_column(BigInteger)
    value_type: Mapped[TagValueType] = mapped_column(Text, nullable=False, default=TagValueType.NUMERIC)
    numeric_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
