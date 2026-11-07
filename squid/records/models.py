"""Persist record titles and their results under revisions of the rules.

A BaseTitle is first, fastest, smallest, fastest smallest, or smallest fastest.
RecordTitle combines a base title, build kind, version scope, and record category
into a stable public identity. TitleDefinition stores its wording under one
RulesRevision, identified by the rules document hash and calculator/formatter
versions. DefinitionFacet stores a definition's taxonomy facets.

ComputationRun groups outcomes for one build kind and version. TitleResult is a
definition's outcome in that run; TitleHolder stores each tied record title holder.
TitleHolderHistory records previous holders. Public queries use the active run,
so result IDs change on recomputation while title IDs remain stable.
"""

import uuid
from enum import StrEnum

from sqlalchemy import (
    UUID,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.queue_models import QueueColumns
from squid.persistence.types import InstantUTC, StrEnumText, now
from squid.records.domain import BaseTitle, BuildKind, ResolutionStatus, VersionScope
from squid.records.domain.models import FacetKind, MaterializationSource


class ComputationStatus(StrEnum):
    """The durable lifecycle state of a record computation run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RulesRevision(Base, kw_only=True):
    """Calculator and formatter versions with a mutable activation timestamp."""

    __tablename__ = "rules_revisions"
    __table_args__ = (
        UniqueConstraint(
            "document_hash",
            "calculator_version",
            "formatter_version",
            name="rules_revisions_content_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    document_hash: Mapped[str] = mapped_column(Text, nullable=False)
    calculator_version: Mapped[str] = mapped_column(Text, nullable=False)
    formatter_version: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class TitleDefinition(Base, kw_only=True):
    """The wording of one stable record title under a rules revision."""

    __tablename__ = "title_definitions"
    __table_args__ = (
        CheckConstraint(
            "materialization_source IN ('eager', 'seeded', 'public_lookup')",
            name="title_definitions_materialization_source_check",
        ),
        UniqueConstraint("rules_revision_id", "title_id", name="title_definitions_identity_key"),
        Index("title_definitions_title_idx", "title_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("record_titles.public_id", name="title_definitions_title_id_fkey", ondelete="RESTRICT"),
        nullable=False,
        default_factory=uuid.uuid4,
    )
    rules_revision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rules_revisions.id", name="title_definitions_rules_revision_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    record_title: Mapped[RecordTitle] = relationship(lazy="raise_on_sql", init=False, repr=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, default=None)
    title_diagnostics: Mapped[list[dict[str, str | list[str]]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default_factory=list
    )
    materialization_source: Mapped[MaterializationSource] = mapped_column(
        StrEnumText(MaterializationSource), nullable=False, server_default=text("'eager'")
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )

    @property
    def base_title(self) -> BaseTitle:
        return self.record_title.base_title

    @property
    def build_kind(self) -> BuildKind:
        return self.record_title.build_kind

    @property
    def version_scope(self) -> VersionScope:
        return self.record_title.version_scope

    @property
    def version_id(self) -> int | None:
        return self.record_title.version_id

    @property
    def category_key(self) -> str:
        return self.record_title.category_key


class RecordTitle(Base, kw_only=True):
    """A stable public identity for a record title across rules revisions."""

    __tablename__ = "record_titles"
    __table_args__ = (
        Index("record_titles_category_idx", "build_kind", "base_title", "category_key"),
        CheckConstraint(
            "base_title IN ('first', 'fastest', 'smallest', 'fastest_smallest', 'smallest_fastest')",
            name="record_titles_base_title_check",
        ),
        CheckConstraint(
            "version_scope IN ('all_time', 'up_to_date')",
            name="record_titles_version_scope_check",
        ),
        CheckConstraint(
            "build_kind IN ('door', 'entrance', 'extender', 'utility')",
            name="record_titles_build_kind_check",
        ),
        UniqueConstraint(
            "base_title",
            "build_kind",
            "version_scope",
            "version_id",
            "category_key",
            name="record_titles_identity_key",
            postgresql_nulls_not_distinct=True,
        ),
    )

    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default_factory=uuid.uuid4
    )
    base_title: Mapped[BaseTitle] = mapped_column(StrEnumText(BaseTitle), nullable=False)
    build_kind: Mapped[BuildKind] = mapped_column(StrEnumText(BuildKind), nullable=False)
    version_scope: Mapped[VersionScope] = mapped_column(StrEnumText(VersionScope), nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("versions.id", name="record_titles_version_id_fkey", ondelete="RESTRICT"),
        default=None,
    )
    category_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class DefinitionFacet(Base, kw_only=True):
    """A canonical taxonomy facet belonging to a record definition."""

    __tablename__ = "title_definition_facets"
    __table_args__ = (
        CheckConstraint(
            "facet_kind IN ('restriction', 'type', 'pattern', 'category')",
            name="title_definition_facets_kind_check",
        ),
        Index("title_definition_facets_lookup_idx", "facet_kind", "facet_id"),
    )

    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "title_definitions.id",
            name="title_definition_facets_definition_id_fkey",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    facet_kind: Mapped[FacetKind] = mapped_column(StrEnumText(FacetKind), primary_key=True)
    facet_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))


class ComputationRun(Base, kw_only=True):
    """A computation attempt for one build kind and version, including its publication state."""

    __tablename__ = "record_computation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="record_computation_runs_status_check",
        ),
        CheckConstraint(
            "build_kind IN ('door', 'entrance', 'extender', 'utility')",
            name="record_computation_runs_build_kind_check",
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
    rules_revision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rules_revisions.id", name="record_computation_runs_rules_revision_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    build_kind: Mapped[BuildKind] = mapped_column(StrEnumText(BuildKind), nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("versions.id", name="record_computation_runs_version_id_fkey", ondelete="RESTRICT"),
        default=None,
    )
    status: Mapped[ComputationStatus] = mapped_column(
        StrEnumText(ComputationStatus), nullable=False, server_default=text("'running'")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    started_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)


class TitleResult(Base, kw_only=True):
    """The outcome for one definition in a computation run."""

    __tablename__ = "title_results"
    __table_args__ = (
        Index("title_results_provisional_build_idx", "provisional_build_id"),
        CheckConstraint(
            "status IN ('resolved', 'unresolved', 'no_candidate')",
            name="title_results_status_check",
        ),
        UniqueConstraint("run_id", "definition_id", name="title_results_run_definition_key"),
        Index("title_results_definition_idx", "definition_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_computation_runs.id", name="title_results_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("title_definitions.id", name="title_results_definition_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ResolutionStatus] = mapped_column(StrEnumText(ResolutionStatus), nullable=False)
    gap_reasons: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    provisional_build_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="title_results_provisional_build_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    history_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    computed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class TitleHolder(Base, kw_only=True):
    """A co-holder of a resolved computed record."""

    __tablename__ = "title_holders"
    __table_args__ = (
        CheckConstraint("rank > 0", name="title_holders_rank_check"),
        Index("title_holders_build_idx", "build_id"),
    )

    result_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("title_results.id", name="title_holders_result_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="title_holders_build_id_fkey", ondelete="RESTRICT"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    metric_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, default=None)
    completion_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class TitleHolderHistory(Base, kw_only=True):
    """A reconstructed interval in a definition's beaten-record chronology."""

    __tablename__ = "title_holder_history"
    __table_args__ = (
        Index("title_holder_history_build_idx", "build_id"),
        CheckConstraint(
            "held_until IS NULL OR held_until >= held_from",
            name="title_holder_history_interval_check",
        ),
        UniqueConstraint(
            "run_id",
            "definition_id",
            "build_id",
            "held_from",
            name="title_holder_history_identity_key",
        ),
        Index("title_holder_history_definition_idx", "definition_id", "held_from"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_computation_runs.id", name="title_holder_history_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("title_definitions.id", name="title_holder_history_definition_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="title_holder_history_build_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    predecessor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "title_holder_history.id",
            name="title_holder_history_predecessor_id_fkey",
            ondelete="SET NULL",
        ),
        default=None,
    )
    held_from: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    held_until: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    metric_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class RecordRecomputeQueueItem(QueueColumns, Base, kw_only=True):
    """A durable request to recompute an affected record scope."""

    __tablename__ = "record_recompute_queue"
    __table_args__ = (
        CheckConstraint(
            "build_kind IN ('door', 'entrance', 'extender', 'utility')",
            name="record_recompute_queue_build_kind_check",
        ),
        Index("record_recompute_queue_build_idx", "build_id"),
        UniqueConstraint("scope_key", name="record_recompute_queue_scope_key_key"),
        Index("record_recompute_queue_ready_idx", "available_at", postgresql_where=text("claimed_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    build_kind: Mapped[BuildKind] = mapped_column(StrEnumText(BuildKind), nullable=False)
    build_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="record_recompute_queue_build_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default_factory=list
    )
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )

    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        server_default=func.now(),
        default_factory=now,
        comment="Earliest next claim time; retry backoff advances it without changing the enqueue time.",
    )

    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        default=None,
        comment="Token identifying the current claim, cleared when new work replaces it.\n\nCompletion and failure updates match this token to preserve work enqueued\nor reclaimed after the worker started.",
    )


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
