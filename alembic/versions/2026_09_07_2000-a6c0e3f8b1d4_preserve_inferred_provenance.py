"""Preserve source messages through draft finalization.

Revision ID: a6c0e3f8b1d4
Revises: f5b9d2e7a0c3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a6c0e3f8b1d4"
down_revision: str = "f5b9d2e7a0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "submission_drafts",
        sa.Column("source_issues", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "submission_drafts",
        sa.Column("source_files", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "submission_drafts",
        sa.Column("source_messages", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM submission_drafts WHERE (source_messages != '[]'::jsonb OR source_files != '[]'::jsonb) AND status != 'submitted')"
        )
    ):
        message = "Cannot drop pending source-message provenance."
        raise RuntimeError(message)
    op.drop_column("submission_drafts", "source_messages")
    op.drop_column("submission_drafts", "source_files")
    op.drop_column("submission_drafts", "source_issues")
