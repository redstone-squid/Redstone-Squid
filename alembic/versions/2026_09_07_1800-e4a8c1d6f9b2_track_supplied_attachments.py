"""Retain supplied-file intent before download or processor registration.

Revision ID: e4a8c1d6f9b2
Revises: d3f7b0c5e8a1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4a8c1d6f9b2"
down_revision: str | Sequence[str] | None = "d3f7b0c5e8a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep failed or interrupted downloads actionable on the shared draft."""
    op.create_table(
        "submission_supplied_attachments",
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submission_drafts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'discarded')", name="submission_supplied_attachments_status_check"
        ),
        sa.CheckConstraint(
            "kind IN ('schematic', 'image', 'video', 'unknown')", name="submission_supplied_attachments_kind_check"
        ),
        comment="A supplied file retained before download and resolved only by registration or explicit discard.",
    )


def downgrade() -> None:
    """Do not erase unresolved supplied-file requirements."""
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM submission_supplied_attachments WHERE status IN ('pending', 'failed'))")
    ):
        message = "Cannot downgrade while supplied attachments need explicit resolution."
        raise RuntimeError(message)
    op.drop_table("submission_supplied_attachments")
