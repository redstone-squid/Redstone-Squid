"""Retain short-lived outcomes for application edit reconciliation."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d0e3f6a9b2c5"
down_revision: str | Sequence[str] | None = "c9d2e5f8a1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "edit_operations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("revisions", postgresql.JSONB(), nullable=False),
        sa.Column("changed", sa.Boolean(), nullable=False),
        sa.Column("final", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("octet_length(fingerprint) = 32", name="edit_operations_fingerprint_length"),
    )
    op.create_index("edit_operations_expiry_idx", "edit_operations", ["expires_at"])


def downgrade() -> None:
    op.drop_table("edit_operations")
