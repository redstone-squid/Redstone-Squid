"""Retain normalized uploads with their committed submission receipt.

Revision ID: f9b3d6e1a4c7
Revises: e8a2c5d0f3b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "f9b3d6e1a4c7"
down_revision: str | Sequence[str] | None = "e8a2c5d0f3b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add durable receipt-to-upload references without republishing any artifact."""
    op.create_table(
        "submission_receipt_media",
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("submission_finalization_results.job_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "upload_id", UUID(as_uuid=True), sa.ForeignKey("media_uploads.id", ondelete="RESTRICT"), primary_key=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        comment="Normalized uploads retained by a committed submission receipt.",
    )
    op.create_index("submission_receipt_media_upload_idx", "submission_receipt_media", ["upload_id"])
    op.execute("""
        INSERT INTO submission_receipt_media (job_id, upload_id)
        SELECT result.job_id, upload.id FROM submission_finalization_results AS result
        JOIN submission_finalization_jobs AS job ON job.id = result.job_id
        JOIN media_uploads AS upload ON upload.draft_id = job.draft_id
        WHERE job.payload->'artifacts'->'normalized_media_upload_ids' @> to_jsonb(ARRAY[upload.id::text])
    """)


def downgrade() -> None:
    """Refuse to erase retained artifact associations."""
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM submission_receipt_media)")):
        message = "Cannot downgrade while submission receipt media is retained."
        raise RuntimeError(message)
    op.drop_table("submission_receipt_media")
