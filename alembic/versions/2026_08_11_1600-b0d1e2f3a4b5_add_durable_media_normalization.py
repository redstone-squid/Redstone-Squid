"""Add durable media normalization.

Revision ID: b0d1e2f3a4b5
Revises: a9c0d1e2f3a4
Create Date: 2026-08-11 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b0d1e2f3a4b5"
down_revision: str | Sequence[str] | None = "a9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist bounded uploads, normalized artifacts, and fenced worker state."""
    op.create_table(
        "media_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_content_type", sa.Text(), nullable=False),
        sa.Column("source_byte_size", sa.BigInteger(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("source_object_key", sa.Text(), nullable=False),
        sa.Column("strip_audio", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("raw_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('image', 'video')", name="media_uploads_kind_check"),
        sa.CheckConstraint("source_byte_size > 0", name="media_uploads_source_size_positive"),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="media_uploads_source_sha256_check",
        ),
        sa.CheckConstraint(
            "char_length(source_content_type) BETWEEN 1 AND 255",
            name="media_uploads_content_type_length",
        ),
        sa.CheckConstraint(
            "kind = 'video' OR strip_audio = false",
            name="media_uploads_audio_only_for_video",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_object_key", name="media_uploads_source_object_key_key"),
        comment="Immutable raw-upload metadata used to verify worker input.",
    )
    op.create_index("media_uploads_draft_idx", "media_uploads", ["draft_id", "created_at"], unique=False)
    op.create_index(
        "media_uploads_raw_cleanup_idx",
        "media_uploads",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("raw_deleted_at IS NULL"),
    )

    op.create_table(
        "media_artifacts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('output', 'poster', 'report')", name="media_artifacts_role_check"),
        sa.CheckConstraint("byte_size > 0", name="media_artifacts_size_positive"),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="media_artifacts_sha256_check",
        ),
        sa.CheckConstraint(
            "(role = 'report' AND width IS NULL AND height IS NULL) OR (role <> 'report' AND width > 0 AND height > 0)",
            name="media_artifacts_dimensions_by_role",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["media_uploads.id"],
            name="media_artifacts_upload_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("upload_id", "role", name="media_artifacts_upload_role_key"),
        comment="Content-addressed normalized output, poster, or disclosure report metadata.",
    )
    op.create_index("media_artifacts_sha256_idx", "media_artifacts", ["sha256"], unique=False)

    op.create_table(
        "media_normalization_jobs",
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'completed', 'dead', 'discarded')",
            name="media_normalization_jobs_status_check",
        ),
        sa.CheckConstraint("attempts >= 0", name="media_normalization_jobs_attempts_nonnegative"),
        sa.CheckConstraint(
            "(status = 'pending' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NULL AND discarded_at IS NULL) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND claim_token IS NOT NULL "
            "AND completed_at IS NULL AND dead_at IS NULL AND discarded_at IS NULL) OR "
            "(status = 'completed' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NOT NULL AND dead_at IS NULL AND discarded_at IS NULL) OR "
            "(status = 'dead' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NOT NULL AND discarded_at IS NULL) OR "
            "(status = 'discarded' AND claimed_at IS NULL AND claim_token IS NULL "
            "AND completed_at IS NULL AND dead_at IS NULL AND discarded_at IS NOT NULL)",
            name="media_normalization_jobs_state_shape",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["media_uploads.id"],
            name="media_normalization_jobs_upload_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("upload_id"),
        comment="A retained, claim-token-fenced request to normalize one raw upload.",
    )
    op.create_index(
        "media_normalization_jobs_ready_idx",
        "media_normalization_jobs",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )
    op.create_index(
        "media_normalization_jobs_terminal_idx",
        "media_normalization_jobs",
        ["upload_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('completed', 'dead', 'discarded')"),
    )


def downgrade() -> None:
    """Remove durable media state."""
    op.drop_index("media_normalization_jobs_terminal_idx", table_name="media_normalization_jobs")
    op.drop_index("media_normalization_jobs_ready_idx", table_name="media_normalization_jobs")
    op.drop_table("media_normalization_jobs")
    op.drop_index("media_artifacts_sha256_idx", table_name="media_artifacts")
    op.drop_table("media_artifacts")
    op.drop_index("media_uploads_raw_cleanup_idx", table_name="media_uploads")
    op.drop_index("media_uploads_draft_idx", table_name="media_uploads")
    op.drop_table("media_uploads")
