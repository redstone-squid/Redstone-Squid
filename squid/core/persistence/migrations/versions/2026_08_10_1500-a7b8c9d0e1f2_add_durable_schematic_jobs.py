"""Add durable schematic jobs.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-10 15:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create a claim-fenced queue whose completed results survive client polling."""
    op.create_table(
        "schematic_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_keys", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_object_key", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("available_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dead_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("error_kind", sa.Text(), nullable=True),
        sa.Column("error_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "operation IN ('capabilities', 'analyze', 'convert', 'compare', 'render', 'simulate', 'autostack')",
            name="schematic_jobs_operation_check",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR dead_at IS NULL",
            name="schematic_jobs_single_terminal_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="A durable request for the worker-owned native schematic engine.",
    )
    op.create_index(
        "schematic_jobs_ready_idx",
        "schematic_jobs",
        ["available_at"],
        postgresql_where=sa.text("completed_at IS NULL AND dead_at IS NULL"),
    )
    op.create_index(
        "schematic_jobs_expiry_idx",
        "schematic_jobs",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove durable schematic work and any retained results."""
    op.drop_index("schematic_jobs_expiry_idx", table_name="schematic_jobs")
    op.drop_index("schematic_jobs_ready_idx", table_name="schematic_jobs")
    op.drop_table("schematic_jobs")
