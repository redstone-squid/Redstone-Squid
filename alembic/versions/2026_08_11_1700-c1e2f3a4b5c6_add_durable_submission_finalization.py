"""Add durable submission finalization.

Revision ID: c1e2f3a4b5c6
Revises: b0d1e2f3a4b5
Create Date: 2026-08-11 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "b0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist revision-pinned finalization work and immutable target results."""
    op.create_table(
        "submission_finalization_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_sha256", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attention_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attention_issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "draft_revision >= 0",
            name="submission_finalization_jobs_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="submission_finalization_jobs_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="submission_finalization_jobs_payload_sha256_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'needs_attention', 'completed', 'dead')",
            name="submission_finalization_jobs_status_check",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND payload IS NOT NULL AND payload_sha256 IS NOT NULL "
            "AND claimed_at IS NULL AND claim_token IS NULL AND claim_expires_at IS NULL "
            "AND completed_at IS NULL AND attention_at IS NULL AND dead_at IS NULL "
            "AND jsonb_array_length(attention_issues) = 0) OR "
            "(status = 'claimed' AND payload IS NOT NULL AND payload_sha256 IS NOT NULL "
            "AND claimed_at IS NOT NULL AND claim_token IS NOT NULL AND claim_expires_at IS NOT NULL "
            "AND completed_at IS NULL AND attention_at IS NULL AND dead_at IS NULL "
            "AND jsonb_array_length(attention_issues) = 0) OR "
            "(status = 'needs_attention' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND claim_expires_at IS NULL AND completed_at IS NULL AND attention_at IS NOT NULL "
            "AND dead_at IS NULL AND jsonb_array_length(attention_issues) > 0) OR "
            "(status = 'completed' AND payload IS NOT NULL AND payload_sha256 IS NOT NULL "
            "AND claimed_at IS NULL AND claim_token IS NULL AND claim_expires_at IS NULL "
            "AND completed_at IS NOT NULL AND attention_at IS NULL AND dead_at IS NULL "
            "AND jsonb_array_length(attention_issues) = 0) OR "
            "(status = 'dead' AND payload IS NOT NULL AND payload_sha256 IS NOT NULL "
            "AND claimed_at IS NULL AND claim_token IS NULL AND claim_expires_at IS NULL "
            "AND completed_at IS NULL AND attention_at IS NULL AND dead_at IS NOT NULL "
            "AND jsonb_array_length(attention_issues) > 0)",
            name="submission_finalization_jobs_state_shape",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["submission_drafts.id"],
            name="submission_finalization_jobs_draft_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", name="submission_finalization_jobs_draft_id_key"),
        comment="A retained source-draft job with UUID-fenced worker claims.",
    )
    op.create_index(
        "submission_finalization_jobs_ready_idx",
        "submission_finalization_jobs",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )
    op.create_index(
        "submission_finalization_jobs_attention_idx",
        "submission_finalization_jobs",
        ["attention_at"],
        unique=False,
        postgresql_where=sa.text("status = 'needs_attention'"),
    )

    op.create_table(
        "submission_finalization_results",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("build_id > 0", name="submission_finalization_results_build_id_positive"),
        sa.CheckConstraint(
            "target_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="submission_finalization_results_target_key_check",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["submission_finalization_jobs.id"],
            name="submission_finalization_results_job_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id"),
        comment="Immutable build identity and target provenance retained after success.",
    )
    op.create_index(
        "submission_finalization_results_build_idx",
        "submission_finalization_results",
        ["build_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove finalization persistence only while no retained work exists."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM submission_finalization_jobs) THEN
                RAISE EXCEPTION 'cannot downgrade while submission finalization work is retained';
            END IF;
        END;
        $$
        """
    )
    op.drop_index("submission_finalization_results_build_idx", table_name="submission_finalization_results")
    op.drop_table("submission_finalization_results")
    op.drop_index("submission_finalization_jobs_attention_idx", table_name="submission_finalization_jobs")
    op.drop_index("submission_finalization_jobs_ready_idx", table_name="submission_finalization_jobs")
    op.drop_table("submission_finalization_jobs")
