"""Persist records and their results under rules revisions.

A RecordCriterion is first, fastest, smallest, fastest smallest, or smallest fastest.
Record combines a criterion, build kind, version scope, and record category
into a stable public identity. RecordDefinition stores its wording under one
RulesRevision, identified by the rules document hash and calculator/formatter
versions. RecordDefinitionTag stores the tags a definition requires.

RecordResult is a record's current outcome: one row per record that some build
competes for, updated in place while the record lives. RecordHolder stores each
holder and RecordTiedBuild each build tied with them. RecordMember indexes every
build in a live record's category, so a build change can find the records it
used to affect. RecordHolderHistory keeps reconstructed holder periods per
record, and survives the record's retirement.

ComputationRun logs one published batch. RecordDrift stores differences that a
backstop recompute found but no queued change explained.
"""

import uuid

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    ColumnElement,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    cast,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import InstrumentedAttribute, Mapped, mapped_column, relationship
from whenever import Instant

from squid.core.persistence.base import Base
from squid.core.persistence.queue_models import QueueColumns
from squid.core.persistence.types import InstantUTC, StrEnumText
from squid.core.records.domain import BuildKind, RecordCriterion, VersionScope
from squid.core.records.domain.door_evidence import DoorRecordEvidence
from squid.core.records.domain.models import MaterializationSource, RecomputeTarget, RecordTagRole
from squid.core.records.persistence_types import DoorRecordEvidenceJSON


class RecordSettings(Base, kw_only=True):
    """The shared current Minecraft version, independent of published runs."""

    __tablename__ = "record_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="record_settings_singleton_check"),
        CheckConstraint("revision >= 0", name="record_settings_revision_check"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False, default=1)
    current_version_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("versions.id", ondelete="RESTRICT"), default=None
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)


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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class RecordDefinition(Base, kw_only=True):
    """The name and tags of a record under a rules revision."""

    __tablename__ = "title_definitions"
    __table_args__ = (
        CheckConstraint(
            "materialization_source IN ('eager', 'seeded', 'public_lookup')",
            name="title_definitions_materialization_source_check",
        ),
        UniqueConstraint("rules_revision_id", "title_id", name="title_definitions_identity_key"),
        Index("title_definitions_title_idx", "title_id"),
        {"comment": "The wording of one stable record title under a rules revision."},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    record_id: Mapped[uuid.UUID] = mapped_column(
        "title_id",
        UUID(as_uuid=True),
        ForeignKey("record_titles.public_id", name="title_definitions_title_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    rules_revision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rules_revisions.id", name="title_definitions_rules_revision_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    record: Mapped[Record] = relationship(lazy="raise_on_sql", init=False, repr=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, default=None)
    title_diagnostics: Mapped[list[dict[str, str | list[str]]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default_factory=list
    )
    materialization_source: Mapped[MaterializationSource] = mapped_column(
        StrEnumText(MaterializationSource), nullable=False, server_default=text("'eager'")
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    @property
    def criterion(self) -> RecordCriterion:
        return self.record.criterion

    @property
    def build_kind(self) -> BuildKind:
        return self.record.build_kind

    @property
    def version_scope(self) -> VersionScope:
        return self.record.version_scope

    @property
    def version_id(self) -> int | None:
        return self.record.version_id

    @property
    def category_key(self) -> str:
        return self.record.category_key


class Record(Base, kw_only=True):
    """A record with a stable public identity across rules revisions."""

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
        {"comment": "A stable public identity for a record title across rules revisions."},
    )

    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default_factory=uuid.uuid4
    )
    criterion: Mapped[RecordCriterion] = mapped_column("base_title", StrEnumText(RecordCriterion), nullable=False)
    build_kind: Mapped[BuildKind] = mapped_column(StrEnumText(BuildKind), nullable=False)
    version_scope: Mapped[VersionScope] = mapped_column(StrEnumText(VersionScope), nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("versions.id", name="record_titles_version_id_fkey", ondelete="RESTRICT"),
        default=None,
    )
    category_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    input_generation: Mapped[int | None] = mapped_column(
        BigInteger,
        default=None,
        comment="The kind's input generation last published or retired here; older batches leave the record alone.",
    )
    retired_at: Mapped[Instant | None] = mapped_column(
        InstantUTC(), default=None, comment="When the record last lost its competitors; null while it has a result."
    )


class RecordDefinitionTag(Base, kw_only=True):
    """A tag required by a record definition."""

    __tablename__ = "title_definition_tags"
    __table_args__ = (
        # Only restrictions identify a record category; patterns and types appear in candidate tags only.
        CheckConstraint("tag_role = 'restriction'", name="title_definition_tags_kind_check"),
        Index("title_definition_tags_lookup_idx", "tag_role", "tag_id"),
    )

    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "title_definitions.id",
            name="title_definition_tags_definition_id_fkey",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    tag_role: Mapped[RecordTagRole] = mapped_column(StrEnumText(RecordTagRole), primary_key=True)
    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tag_definitions.id", name="title_definition_tags_tag_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))


class RecordInputGeneration(Base, kw_only=True):
    """A permanent per-kind counter changed atomically with record invalidation."""

    __tablename__ = "record_input_generations"
    __table_args__ = (
        CheckConstraint("build_kind IN ('door', 'extender')", name="record_input_generations_kind_check"),
        CheckConstraint("generation >= 0", name="record_input_generations_generation_check"),
    )

    build_kind: Mapped[BuildKind] = mapped_column(StrEnumText(BuildKind), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))


class ComputationRun(Base, kw_only=True):
    """One published batch of one build kind; results and drift name the run that wrote them."""

    __tablename__ = "record_computation_runs"
    __table_args__ = (
        CheckConstraint(
            "build_kind IN ('door', 'entrance', 'extender', 'utility')",
            name="record_computation_runs_build_kind_check",
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
    full: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    input_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    completed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class RecordResult(Base, kw_only=True):
    """A live record's current outcome; a record without competitors has none."""

    __tablename__ = "title_results"
    __table_args__ = (
        CheckConstraint(
            "door_evidence IS NULL OR (jsonb_typeof(door_evidence) = 'object' "
            "AND door_evidence->'schema_version' = '1'::jsonb) IS TRUE",
            name="title_results_door_evidence_check",
        ),
        UniqueConstraint("title_id", name="title_results_title_key"),
        Index("title_results_definition_idx", "definition_id"),
        Index("title_results_run_idx", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    record_id: Mapped[uuid.UUID] = mapped_column(
        "title_id",
        UUID(as_uuid=True),
        ForeignKey("record_titles.public_id", name="title_results_title_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_computation_runs.id", name="title_results_run_id_fkey", ondelete="RESTRICT"),
        nullable=False,
        comment="The batch that last changed this result.",
    )
    definition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("title_definitions.id", name="title_results_definition_id_fkey", ondelete="RESTRICT"),
        nullable=False,
    )
    gap_reasons: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default_factory=dict
    )
    door_evidence: Mapped[DoorRecordEvidence | None] = mapped_column(DoorRecordEvidenceJSON(), default=None)
    history_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    fingerprint: Mapped[str] = mapped_column(
        Text, nullable=False, comment="A digest of the published contents, to skip republishing unchanged results."
    )
    computed_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    last_checked_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    @classmethod
    def matches_tag(cls, tag_id: int | InstrumentedAttribute[int]) -> ColumnElement[bool]:
        restriction = (
            select(RecordDefinitionTag.tag_id)
            .where(RecordDefinitionTag.definition_id == cls.definition_id, RecordDefinitionTag.tag_id == tag_id)
            .correlate_except(RecordDefinitionTag)
            .exists()
        )
        # Patterns live in the category key as whole IDs inside t[3,17].
        identity = literal(str(tag_id)) if isinstance(tag_id, int) else cast(tag_id, Text)
        pattern = Record.category_key.regexp_match(literal(r"\|t\[([0-9]+,)*") + identity + r"(,[0-9]+)*\]")
        return or_(restriction, pattern)


class RecordMember(Base, kw_only=True):
    """A build in a live record's category, whether or not it competes.

    There is no foreign key to builds: a deleted build's rows must survive until
    a worker recomputes the records it leaves.
    """

    __tablename__ = "record_members"
    __table_args__ = (Index("record_members_build_idx", "build_id"),)

    record_id: Mapped[uuid.UUID] = mapped_column(
        "title_id",
        UUID(as_uuid=True),
        ForeignKey("record_titles.public_id", name="record_members_title_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    build_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class RecordHolder(Base, kw_only=True):
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


class RecordTiedBuild(Base, kw_only=True):
    """A build that matches a record's holders on every metric but was completed later."""

    __tablename__ = "title_tied_builds"
    __table_args__ = (Index("title_tied_builds_build_idx", "build_id"),)

    result_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("title_results.id", name="title_tied_builds_result_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    build_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("builds.id", name="title_tied_builds_build_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )


class RecordHolderHistory(Base, kw_only=True):
    """A reconstructed interval in a record's beaten-record chronology.

    Each publication replaces a record's periods. Retirement keeps them and
    closes the open one.
    """

    __tablename__ = "title_holder_history"
    __table_args__ = (
        Index("title_holder_history_build_idx", "build_id"),
        CheckConstraint(
            "held_until IS NULL OR held_until >= held_from",
            name="title_holder_history_interval_check",
        ),
        UniqueConstraint("title_id", "build_id", "held_from", name="title_holder_history_identity_key"),
        Index("title_holder_history_definition_idx", "definition_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    record_id: Mapped[uuid.UUID] = mapped_column(
        "title_id",
        UUID(as_uuid=True),
        ForeignKey("record_titles.public_id", name="title_holder_history_title_id_fkey", ondelete="CASCADE"),
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


class RecordDrift(Base, kw_only=True):
    """A record a backstop recompute changed although no queued change explained it."""

    __tablename__ = "record_drift"
    __table_args__ = (Index("record_drift_title_idx", "title_id"), Index("record_drift_run_id_idx", "run_id"))

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_computation_runs.id", name="record_drift_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        "title_id",
        UUID(as_uuid=True),
        ForeignKey("record_titles.public_id", name="record_drift_title_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    detected_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    published: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, comment="Holders, gaps and members before the backstop; empty for a retired record."
    )
    computed: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, comment="The same facts as the backstop computed them; empty when it retired the record."
    )


class RecordRecomputeQueueItem(QueueColumns, Base, kw_only=True):
    """A durable request to recompute what one change affects.

    `scope_key` deduplicates requests: the kind alone for a whole-kind
    recompute, otherwise the kind and target, such as `door:build:12`.
    """

    __tablename__ = "record_recompute_queue"
    __table_args__ = (
        CheckConstraint(
            "build_kind IN ('door', 'entrance', 'extender', 'utility')",
            name="record_recompute_queue_build_kind_check",
        ),
        CheckConstraint(
            "target IN ('kind', 'backstop', 'build', 'record') "
            "AND (build_id IS NOT NULL) = (target = 'build') AND (title_id IS NOT NULL) = (target = 'record')",
            name="record_recompute_queue_target_check",
        ),
        Index("record_recompute_queue_build_idx", "build_id"),
        Index("record_recompute_queue_title_idx", "title_id"),
        Index("record_recompute_queue_kind_idx", "build_kind"),
        UniqueConstraint("scope_key", name="record_recompute_queue_scope_key_key"),
        Index("record_recompute_queue_ready_idx", "available_at", postgresql_where=text("claimed_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    build_kind: Mapped[BuildKind] = mapped_column(StrEnumText(BuildKind), nullable=False)
    target: Mapped[RecomputeTarget] = mapped_column(
        StrEnumText(RecomputeTarget), nullable=False, server_default=text("'kind'"), default=RecomputeTarget.KIND
    )
    # No foreign key: the deletion of a build is itself work to process.
    build_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        "title_id",
        UUID(as_uuid=True),
        ForeignKey("record_titles.public_id", name="record_recompute_queue_title_id_fkey", ondelete="CASCADE"),
        default=None,
    )
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default_factory=list
    )
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        server_default=func.now(),
        default_factory=Instant.now,
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


class DoorRangeRegistration(Base, kw_only=True):
    """A registered range query and its latest reserved computation generation."""

    __tablename__ = "door_range_registrations"
    __table_args__ = (
        UniqueConstraint("query_key", name="door_range_registrations_query_key_key"),
        CheckConstraint("generation >= 0", name="door_range_registrations_generation_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    query_key: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    last_checked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"), default=0)


class DoorRangeRecomputeQueueItem(QueueColumns, Base, kw_only=True):
    """One independently retried refresh request per registered door range."""

    __tablename__ = "door_range_recompute_queue"

    query_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("door_range_registrations.id", ondelete="CASCADE"), primary_key=True
    )
    enqueued_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class DoorRangeRun(Base, kw_only=True):
    """A reserved attempt whose complete result can replace one registered query."""

    __tablename__ = "door_range_runs"
    __table_args__ = (
        UniqueConstraint("query_id", "generation", name="door_range_runs_generation_key"),
        CheckConstraint("generation > 0", name="door_range_runs_generation_check"),
        CheckConstraint(
            "(result IS NOT NULL OR error IS NOT NULL) = (completed_at IS NOT NULL)",
            name="door_range_runs_completion_check",
        ),
        CheckConstraint(
            "NOT is_active OR (result->'analysis'->>'status' = 'exact') IS TRUE",
            name="door_range_runs_active_check",
        ),
        Index("door_range_runs_one_active_idx", "query_id", unique=True, postgresql_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    query_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("door_range_registrations.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rules_revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rules_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)


class DoorRangeSnapshot(Base, kw_only=True):
    """Immutable arithmetic facts captured before a range worker starts."""

    __tablename__ = "door_range_snapshots"
    __table_args__ = (
        CheckConstraint(
            "(jsonb_typeof(document) = 'object' AND document->'schema_version' = '1'::jsonb) IS TRUE",
            name="door_range_snapshots_version_check",
        ),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("door_range_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
