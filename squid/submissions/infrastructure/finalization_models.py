"""SQLAlchemy models for durable submission finalization."""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, StrEnumText, now
from squid.submissions.domain import FinalizationJobStatus

_LEGACY_SPONSOR_FORBIDDEN_SQL = """
payload IS NULL OR NOT (
    payload ->> 'payload_schema' = '1'
    AND payload ->> 'sponsor_attribution' = 'true'
)
"""

_FINALIZATION_STATE_SHAPE_SQL = """
(
    status = 'pending'
    AND payload IS NOT NULL
    AND payload_sha256 IS NOT NULL
    AND claimed_at IS NULL
    AND claim_token IS NULL
    AND claim_expires_at IS NULL
    AND completed_at IS NULL
    AND attention_at IS NULL
    AND dead_at IS NULL
    AND jsonb_array_length(attention_issues) = 0
) OR (
    status = 'claimed'
    AND payload IS NOT NULL
    AND payload_sha256 IS NOT NULL
    AND claimed_at IS NOT NULL
    AND claim_token IS NOT NULL
    AND claim_expires_at IS NOT NULL
    AND completed_at IS NULL
    AND attention_at IS NULL
    AND dead_at IS NULL
    AND jsonb_array_length(attention_issues) = 0
) OR (
    status = 'needs_attention'
    AND claimed_at IS NULL
    AND claim_token IS NULL
    AND claim_expires_at IS NULL
    AND completed_at IS NULL
    AND attention_at IS NOT NULL
    AND dead_at IS NULL
    AND jsonb_array_length(attention_issues) > 0
) OR (
    status = 'completed'
    AND payload IS NOT NULL
    AND payload_sha256 IS NOT NULL
    AND claimed_at IS NULL
    AND claim_token IS NULL
    AND claim_expires_at IS NULL
    AND completed_at IS NOT NULL
    AND attention_at IS NULL
    AND dead_at IS NULL
    AND jsonb_array_length(attention_issues) = 0
) OR (
    status = 'dead'
    AND payload IS NOT NULL
    AND payload_sha256 IS NOT NULL
    AND claimed_at IS NULL
    AND claim_token IS NULL
    AND claim_expires_at IS NULL
    AND completed_at IS NULL
    AND attention_at IS NULL
    AND dead_at IS NOT NULL
    AND jsonb_array_length(attention_issues) > 0
)
"""


class SubmissionFinalizationJob(Base, kw_only=True):
    """A retained source-draft job with UUID-fenced worker claims."""

    __tablename__ = "submission_finalization_jobs"
    __table_args__ = (
        UniqueConstraint("draft_id", name="submission_finalization_jobs_draft_id_key"),
        CheckConstraint("draft_revision >= 0", name="submission_finalization_jobs_revision_nonnegative"),
        CheckConstraint("attempts >= 0", name="submission_finalization_jobs_attempts_nonnegative"),
        CheckConstraint(
            "payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="submission_finalization_jobs_payload_sha256_check",
        ),
        CheckConstraint(
            _LEGACY_SPONSOR_FORBIDDEN_SQL,
            name="submission_finalization_jobs_legacy_sponsor_forbidden",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'needs_attention', 'completed', 'dead')",
            name="submission_finalization_jobs_status_check",
        ),
        CheckConstraint(
            _FINALIZATION_STATE_SHAPE_SQL,
            name="submission_finalization_jobs_state_shape",
        ),
        Index(
            "submission_finalization_jobs_ready_idx",
            "available_at",
            postgresql_where=text("status IN ('pending', 'claimed')"),
        ),
        Index(
            "submission_finalization_jobs_attention_idx",
            "attention_at",
            postgresql_where=text("status = 'needs_attention'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submission_drafts.id", name="submission_finalization_jobs_draft_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=None)
    payload_sha256: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[FinalizationJobStatus] = mapped_column(StrEnumText(FinalizationJobStatus), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    available_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    claim_expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    attention_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    dead_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    attention_issues: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default_factory=list)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class SubmissionFinalizationResult(Base, kw_only=True):
    """Immutable build identity retained after successful finalization."""

    __tablename__ = "submission_finalization_results"
    __table_args__ = (
        CheckConstraint("build_id > 0", name="submission_finalization_results_build_id_positive"),
        CheckConstraint(
            "target_key IS NULL OR target_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="submission_finalization_results_target_key_check",
        ),
        Index("submission_finalization_results_build_idx", "build_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "submission_finalization_jobs.id", name="submission_finalization_results_job_id_fkey", ondelete="CASCADE"
        ),
        primary_key=True,
    )
    build_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    _legacy_target_key: Mapped[str | None] = mapped_column("target_key", Text, default=None, deferred=True)
    _legacy_provenance: Mapped[dict[str, object] | None] = mapped_column(
        "provenance", JSONB, default=None, deferred=True
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
