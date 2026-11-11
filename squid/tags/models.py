"""Store tag definitions and the values assigned to individual builds.

A TagDefinition describes a restriction, pattern, or showcase label and whether
it accepts a numeric, text, or boolean value. TagAssignment applies that definition
to one build; a valueless tag records presence alone. Official definitions can
participate in record rules, while user-defined tags are limited to showcase labels.

The display name is presentation text, the stable key identifies the definition,
and the optional query name exposes it as a search field. Numeric assignments use
the definition's canonical unit even when another unit is chosen for display.
"""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.mixins import AuditTimestampsMixin, CreatedAtMixin
from squid.persistence.types import InstantUTC, StrEnumText
from squid.tags.domain import (
    RecordOperator,
    TagAuthority,
    TagModerationStatus,
    TagSemanticKind,
    TagValue,
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


class TagDefinition(AuditTimestampsMixin, Base, kw_only=True):
    """A canonical tag that may be assigned to builds."""

    __tablename__ = "tag_definitions"
    __table_args__ = (
        Index("tag_definitions_created_by_idx", "created_by_account_id"),
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
        # Numeric user tags may have no unit or step. Only non-numeric tags must
        # leave all numeric metadata empty.
        CheckConstraint(
            "value_type = 'numeric' OR "
            "(canonical_unit_key IS NULL AND default_display_unit_key IS NULL AND numeric_step IS NULL)",
            name="tag_definitions_non_numeric_unit_check",
        ),
        CheckConstraint(
            "numeric_step IS NULL OR numeric_step > 0",
            name="tag_definitions_numeric_step_check",
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    query_name: Mapped[str | None] = mapped_column(Text, default=None)
    authority: Mapped[TagAuthority] = mapped_column(StrEnumText(TagAuthority), nullable=False)
    semantic_kind: Mapped[TagSemanticKind] = mapped_column(StrEnumText(TagSemanticKind), nullable=False)
    restriction_type: Mapped[str | None] = mapped_column(Text, default=None)
    value_type: Mapped[TagValueType] = mapped_column(StrEnumText(TagValueType), nullable=False)
    record_operator: Mapped[RecordOperator | None] = mapped_column(StrEnumText(RecordOperator), default=None)
    canonical_unit: Mapped[str | None] = mapped_column(
        "canonical_unit_key",
        Text,
        ForeignKey("tag_units.key", name="tag_definitions_canonical_unit_fkey", ondelete="RESTRICT"),
        default=None,
    )
    default_display_unit: Mapped[str | None] = mapped_column(
        "default_display_unit_key",
        Text,
        ForeignKey("tag_units.key", name="tag_definitions_display_unit_fkey", ondelete="RESTRICT"),
        default=None,
    )
    canonical_unit_definition: Mapped[UnitDefinition | None] = relationship(
        foreign_keys=[canonical_unit], lazy="raise", init=False
    )
    default_display_unit_definition: Mapped[UnitDefinition | None] = relationship(
        foreign_keys=[default_display_unit], lazy="raise", init=False
    )
    numeric_step: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    render_template: Mapped[str] = mapped_column(Text, nullable=False, default="{name}")
    default_display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    moderation_status: Mapped[TagModerationStatus] = mapped_column(StrEnumText(TagModerationStatus), nullable=False)
    created_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", name="tag_definitions_created_by_account_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    archived_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)

    aliases: Mapped[list[TagAlias]] = relationship(
        back_populates="definition", default_factory=list, lazy="raise_on_sql"
    )
    applicabilities: Mapped[list[TagApplicability]] = relationship(
        back_populates="definition", default_factory=list, lazy="raise_on_sql"
    )
    assignments: Mapped[list[TagAssignment]] = relationship(
        back_populates="definition", default_factory=list, lazy="raise_on_sql", repr=False
    )

    def __post_init__(self) -> None:
        if not self.normalized_name:
            self.normalized_name = " ".join(self.display_name.casefold().split())


class TagAlias(CreatedAtMixin, Base, kw_only=True):
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

    definition: Mapped[TagDefinition] = relationship(back_populates="aliases", lazy="raise_on_sql", default=None)


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

    definition: Mapped[TagDefinition] = relationship(
        back_populates="applicabilities", lazy="raise_on_sql", default=None
    )


class TagAssignment(AuditTimestampsMixin, Base, kw_only=True, init=False):
    """A typed tag value attached to one build."""

    __tablename__ = "build_tag_assignments"
    __table_args__ = (
        Index("build_tag_assignments_created_by_idx", "created_by_account_id"),
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
    value_type: Mapped[TagValueType] = mapped_column(StrEnumText(TagValueType), nullable=False)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric, default=None)
    text_value: Mapped[str | None] = mapped_column(Text, default=None)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, default=None)
    display_unit: Mapped[str | None] = mapped_column(
        "display_unit_key",
        Text,
        ForeignKey("tag_units.key", name="build_tag_assignments_display_unit_fkey", ondelete="RESTRICT"),
        default=None,
    )
    """Unit used for presentation; the stored numeric value uses the definition's canonical unit."""
    display_unit_definition: Mapped[UnitDefinition | None] = relationship(
        foreign_keys=[display_unit], lazy="raise", init=False
    )
    display_order: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    evidence: Mapped[str | None] = mapped_column(Text, default=None)
    provenance: Mapped[str] = mapped_column(Text, nullable=False, default="submitted")
    created_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", name="build_tag_assignments_created_by_account_id_fkey", ondelete="SET NULL"),
        default=None,
    )

    definition: Mapped[TagDefinition] = relationship(
        back_populates="assignments", lazy="raise_on_sql", default=None, cascade=""
    )

    def __init__(
        self,
        definition: TagDefinition,
        value: TagValue = None,
        display_unit: str | None = None,
        display_order: int | None = None,
        evidence: str | None = None,
        provenance: str = "submitted",
        *,
        created_by_account_id: int | None = None,
    ) -> None:
        self.definition = definition
        self.value_type = definition.value_type
        self.value = value
        self.display_unit = display_unit
        self.display_order = display_order
        self.evidence = evidence
        self.provenance = provenance
        self.created_by_account_id = created_by_account_id
        self.created_at = self.updated_at = Instant.now()

    @property
    def presentation_unit(self) -> UnitDefinition | None:
        """The requested display unit, falling back to the definition's default and canonical unit."""
        return (
            self.display_unit_definition
            or self.definition.default_display_unit_definition
            or self.definition.canonical_unit_definition
        )

    @property
    def presentation_value(self) -> TagValue:
        """The value converted for presentation; stored values remain canonical."""
        return convert_display_value(self.value, self.definition.canonical_unit_definition, self.presentation_unit)

    @property
    def value(self) -> TagValue:
        """The scalar stored in the column selected by the definition's value type."""
        match self.value_type:
            case TagValueType.NONE:
                return None
            case TagValueType.NUMERIC:
                return self.numeric_value
            case TagValueType.TEXT:
                return self.text_value
            case TagValueType.BOOLEAN:
                return self.boolean_value

    @value.setter
    def value(self, value: TagValue) -> None:
        """Replace the stored scalar with a value of the declared type.

        All value columns are cleared before validation. An invalid assignment
        raises ValueError and leaves those columns empty.
        """
        self.numeric_value = self.text_value = self.boolean_value = None
        match self.value_type, value:
            case TagValueType.NONE, None:
                pass
            case TagValueType.NUMERIC, Decimal():
                self.numeric_value = value
            case TagValueType.TEXT, str():
                self.text_value = value
            case TagValueType.BOOLEAN, bool():
                self.boolean_value = value
            case _:
                msg = f"Invalid {self.value_type.value} tag value: {value!r}"
                raise ValueError(msg)


class TagRelation(Base, kw_only=True):
    """A semantic relationship between official restrictions."""

    __tablename__ = "tag_relations"
    __table_args__ = (
        Index("tag_relations_target_idx", "target_tag_id"),
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
    value_type: Mapped[TagValueType] = mapped_column(
        StrEnumText(TagValueType), nullable=False, default=TagValueType.NUMERIC
    )
    numeric_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def convert_display_value(
    value: TagValue, canonical: UnitDefinition | None, display: UnitDefinition | None
) -> TagValue:
    """Convert a numeric canonical value to a compatible display unit."""
    if not isinstance(value, Decimal):
        return value
    return value * display_conversion_scale(canonical, display)


def display_conversion_scale(canonical: UnitDefinition | None, display: UnitDefinition | None) -> Decimal:
    """Return the factor for rendering a canonical value in a compatible display unit."""
    if canonical is None or display is None:
        return Decimal(1)
    if canonical.dimension != display.dimension:
        msg = "Display and canonical units must have the same dimension"
        raise ValueError(msg)
    return canonical.scale_to_base / display.scale_to_base
