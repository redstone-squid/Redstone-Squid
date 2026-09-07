"""Retain independent draft references to normalized media.

Revision ID: d3f7b0c5e8a1
Revises: c2e6a9b4d7f0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3f7b0c5e8a1"
down_revision: str | Sequence[str] | None = "c2e6a9b4d7f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill retained uploads without reviving deleted drafts or discarded jobs."""
    op.create_table(
        "media_draft_references",
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submission_drafts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_uploads.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("discarded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        comment="One independently discardable draft reference to a shared normalization job.",
    )
    op.create_index("media_draft_references_upload_idx", "media_draft_references", ["upload_id"])
    op.execute("""INSERT INTO media_draft_references (draft_id, upload_id, discarded)
        SELECT upload.draft_id, upload.id, COALESCE(job.status = 'discarded', false)
        FROM media_uploads AS upload JOIN submission_drafts AS draft ON draft.id = upload.draft_id
        LEFT JOIN media_normalization_jobs AS job ON job.upload_id = upload.id""")


def downgrade() -> None:
    """Refuse to lose shared ownership during rollback."""
    if op.get_bind().scalar(
        sa.text("""SELECT EXISTS (SELECT 1 FROM media_draft_references AS ref
        JOIN media_uploads AS upload ON upload.id = ref.upload_id WHERE ref.draft_id <> upload.draft_id)""")
    ):
        message = "Cannot downgrade while shared media references are retained."
        raise RuntimeError(message)
    op.drop_table("media_draft_references")
