"""Track reference-safe cleanup of normalized media objects.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-08-11 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Register every content-addressed object for durable, reference-fenced cleanup."""
    op.create_index("media_artifacts_object_key_idx", "media_artifacts", ["object_key"], unique=False)
    op.create_table(
        "media_artifact_objects",
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("first_upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="media_artifact_objects_attempts_nonnegative"),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="media_artifact_objects_sha256_check",
        ),
        sa.CheckConstraint("byte_size > 0", name="media_artifact_objects_size_positive"),
        sa.CheckConstraint(
            "(cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL)",
            name="media_artifact_objects_cleanup_claim_shape",
        ),
        sa.PrimaryKeyConstraint("object_key"),
    )
    op.create_table_comment(
        "media_artifact_objects",
        "Durable lifecycle and cleanup audit state for one content-addressed object.",
        existing_comment=None,
        schema=None,
    )
    op.create_index(
        "media_artifact_objects_cleanup_idx",
        "media_artifact_objects",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "media_artifact_publications",
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="media_artifact_publications_expiry_after_creation",
        ),
        sa.ForeignKeyConstraint(
            ["object_key"],
            ["media_artifact_objects.object_key"],
            name="media_artifact_publications_object_key_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("object_key", "upload_id", "claim_token"),
    )
    op.create_table_comment(
        "media_artifact_publications",
        "A crash-recoverable lease protecting one claim's in-flight object publication.",
        existing_comment=None,
        schema=None,
    )
    op.create_index(
        "media_artifact_publications_expiry_idx",
        "media_artifact_publications",
        ["expires_at"],
        unique=False,
    )
    # Rolling-deploy workers from before this revision publish without leases. Delay the
    # backfill by one full publication lease so their in-flight puts can settle safely.
    op.execute(
        sa.text(
            """
            INSERT INTO media_artifact_objects (
                object_key,
                sha256,
                byte_size,
                first_upload_id,
                last_upload_id,
                available_at,
                first_seen_at,
                last_seen_at
            )
            SELECT DISTINCT ON (object_key)
                object_key,
                sha256,
                byte_size,
                upload_id,
                upload_id,
                now() + interval '24 hours',
                created_at,
                created_at
            FROM media_artifacts
            ORDER BY object_key, created_at, id
            """
        )
    )


def downgrade() -> None:
    """Remove normalized-media cleanup lifecycle state."""
    op.drop_index("media_artifact_publications_expiry_idx", table_name="media_artifact_publications")
    op.drop_table("media_artifact_publications")
    op.drop_index("media_artifact_objects_cleanup_idx", table_name="media_artifact_objects")
    op.drop_table("media_artifact_objects")
    op.drop_index("media_artifacts_object_key_idx", table_name="media_artifacts")
