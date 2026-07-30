"""SQLAlchemy models for rule-driven record computation."""

from __future__ import annotations

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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class RecordRuleset(Base, kw_only=True):
    """An immutable version of the record calculators and title formatters."""

    __tablename__ = "record_rulesets"
    __table_args__ = (
        UniqueConstraint(
            "document_hash",
            "calculator_version",
            "formatter_version",
            name="record_rulesets_content_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    document_hash: Mapped[str] = mapped_column(Text, nullable=False)
    calculator_version: Mapped[str] = mapped_column(Text, nullable=False)
    formatter_version: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class RecordDefinition(Base, kw_only=True):
    """A stable identity for one record competition."""

    __tablename__ = "record_definitions"
    __table_args__ = (
        CheckConstraint(
            "record_class IN ('smallest', 'fastest')",
            name="record_definitions_record_class_check",
        ),
        CheckConstraint(
            "version_scope IN ('all_time', 'current')",
            name="record_definitions_version_scope_check",
        ),
        CheckConstraint(
            "materialization_source IN ('eager', 'seeded', 'public_lookup')",
            name="record_definitions_materialization_source_check",
        ),
        UniqueConstraint(
            "ruleset_id",
            "record_class",
            "build_kind",
            "version_scope",
            "version_id",
            "category_key",
            name="record_definitions_identity_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("record_definitions_category_idx", "build_kind", "record_class", "category_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    ruleset_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_rulesets.id", name="record_definitions_ruleset_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    record_class: Mapped[str] = mapped_column(Text, nullable=False)
    build_kind: Mapped[str] = mapped_column(Text, nullable=False)
    version_scope: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("versions.id", name="record_definitions_version_id_fkey", ondelete="RESTRICT"),
        default=None,
    )
    category_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, default=None)
    title_diagnostics: Mapped[list[dict[str, str | list[str]]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default_factory=list
    )
    materialization_source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'eager'"))
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class RecordDefinitionFacet(Base, kw_only=True):
    """A canonical taxonomy facet belonging to a record definition."""

    __tablename__ = "record_definition_facets"
    __table_args__ = (
        CheckConstraint(
            "facet_kind IN ('restriction', 'type', 'pattern', 'category')",
            name="record_definition_facets_kind_check",
        ),
        Index("record_definition_facets_lookup_idx", "facet_kind", "facet_id"),
    )

    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "record_definitions.id",
            name="record_definition_facets_definition_id_fkey",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    facet_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    facet_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))


class RecordComputationRun(Base, kw_only=True):
    """An immutable attempt to calculate records for one build and version scope."""

    __tablename__ = "record_computation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="record_computation_runs_status_check",
        ),
        Index(
            "record_computation_runs_one_active_idx",
            "build_kind",
            "version_id",
            unique=True,
            postgresql_where=text("is_active"),
            postgresql_nulls_not_distinct=True,
        ),
        Index("record_computation_runs_started_idx", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    ruleset_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_rulesets.id", name="record_computation_runs_ruleset_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    build_kind: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("versions.id", name="record_computation_runs_version_id_fkey", ondelete="RESTRICT"),
        default=None,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    started_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)


class RecordResult(Base, kw_only=True):
    """The outcome for one definition in a computation run."""

    __tablename__ = "record_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('resolved', 'unresolved', 'no_candidate')",
            name="record_results_status_check",
        ),
        UniqueConstraint("run_id", "definition_id", name="record_results_run_definition_key"),
        Index("record_results_definition_idx", "definition_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_computation_runs.id", name="record_results_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_definitions.id", name="record_results_definition_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    gap_reasons: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    provisional_build_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="record_results_provisional_build_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    history_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    computed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class RecordResultHolder(Base, kw_only=True):
    """A co-holder of a resolved computed record."""

    __tablename__ = "record_result_holders"
    __table_args__ = (
        CheckConstraint("rank > 0", name="record_result_holders_rank_check"),
        Index("record_result_holders_build_idx", "build_id"),
    )

    result_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_results.id", name="record_result_holders_result_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="record_result_holders_build_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    metric_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, default=None)
    completion_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class RecordHolderHistory(Base, kw_only=True):
    """A reconstructed interval in a definition's beaten-record chronology."""

    __tablename__ = "record_holder_history"
    __table_args__ = (
        CheckConstraint(
            "held_until IS NULL OR held_until >= held_from",
            name="record_holder_history_interval_check",
        ),
        UniqueConstraint(
            "run_id",
            "definition_id",
            "build_id",
            "held_from",
            name="record_holder_history_identity_key",
        ),
        Index("record_holder_history_definition_idx", "definition_id", "held_from"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_computation_runs.id", name="record_holder_history_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_definitions.id", name="record_holder_history_definition_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="record_holder_history_build_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    predecessor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "record_holder_history.id",
            name="record_holder_history_predecessor_id_fkey",
            ondelete="SET NULL",
        ),
        default=None,
    )
    held_from: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    held_until: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    metric_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class RecordRecomputeQueueItem(Base, kw_only=True):
    """A durable request to recompute an affected record scope."""

    __tablename__ = "record_recompute_queue"
    __table_args__ = (
        UniqueConstraint("scope_key", name="record_recompute_queue_scope_key_key"),
        Index("record_recompute_queue_ready_idx", "enqueued_at", postgresql_where=text("locked_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    build_kind: Mapped[str] = mapped_column(Text, nullable=False)
    build_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="record_recompute_queue_build_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default_factory=list
    )
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    locked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class DoorTimingVariant(Base, kw_only=True):
    """A measured door timing variant used for lexicographic fastest records."""

    __tablename__ = "door_timing_variants"
    __table_args__ = (UniqueConstraint("build_id", "label", name="door_timing_variants_build_label_key"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("doors.build_id", name="door_timing_variants_build_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'default'"))
    opening_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    visible_opening_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    closing_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    visible_closing_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    opening_reset_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    closing_reset_time: Mapped[int | None] = mapped_column(BigInteger, default=None)


class ExtenderTimingVariant(Base, kw_only=True):
    """A measured piston-extender timing variant used for fastest records."""

    __tablename__ = "extender_timing_variants"
    __table_args__ = (UniqueConstraint("build_id", "label", name="extender_timing_variants_build_label_key"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("extenders.build_id", name="extender_timing_variants_build_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'default'"))
    retraction_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    extension_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    retraction_reset_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    extension_reset_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
