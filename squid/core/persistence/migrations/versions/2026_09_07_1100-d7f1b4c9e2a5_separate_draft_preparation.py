"""Separate draft preparation issues from execution attempts.

Revision ID: d7f1b4c9e2a5
Revises: c6e0a3b8d1f4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "d7f1b4c9e2a5"
down_revision: str | Sequence[str] | None = "c6e0a3b8d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Copy current correction issues without discarding historical jobs."""
    op.add_column("submission_drafts", sa.Column("preparation_issues", JSONB(), nullable=False, server_default="[]"))
    op.add_column("submission_drafts", sa.Column("preparation_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "submission_drafts_preparation_issues_array", "submission_drafts", "jsonb_typeof(preparation_issues) = 'array'"
    )
    op.create_index(
        "submission_drafts_preparation_ready_idx",
        "submission_drafts",
        ["preparation_retry_at"],
        postgresql_where=sa.text("preparation_retry_at IS NOT NULL"),
    )
    op.execute("""
        UPDATE submission_drafts AS draft SET preparation_issues = latest.attention_issues
        FROM (
            SELECT DISTINCT ON (draft_id) draft_id, attention_issues, draft_revision
            FROM submission_finalization_jobs ORDER BY draft_id, attempt_number DESC
        ) AS latest
        WHERE draft.id = latest.draft_id AND draft.revision = latest.draft_revision
            AND draft.status = 'needs_attention'
    """)


def downgrade() -> None:
    """Refuse to lose requests and issues that older workers cannot interpret."""
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM submission_drafts
        WHERE preparation_retry_at IS NOT NULL OR preparation_issues <> '[]'::jsonb)
    """)
    ):
        message = "Cannot downgrade while draft preparation state is retained."
        raise RuntimeError(message)
    op.drop_index("submission_drafts_preparation_ready_idx", table_name="submission_drafts")
    op.drop_constraint("submission_drafts_preparation_issues_array", "submission_drafts", type_="check")
    op.drop_column("submission_drafts", "preparation_retry_at")
    op.drop_column("submission_drafts", "preparation_issues")
