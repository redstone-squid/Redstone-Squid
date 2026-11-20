"""Add moderator-triggered schematic simulation evidence.

Revision ID: d9f6a8b2c4e1
Revises: c8d5e7f1a2b3
Create Date: 2026-08-05 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d9f6a8b2c4e1"
down_revision: str | Sequence[str] | None = "c8d5e7f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the latest simulation evidence beside its analyzed schematic."""
    op.create_table_comment(
        "schematic_renders",
        "A replaceable preview artifact keyed by the complete rendering recipe.",
        existing_comment=None,
        schema=None,
    )
    op.add_column(
        "build_schematics",
        sa.Column("simulation_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Remove persisted simulation evidence."""
    op.drop_column("build_schematics", "simulation_evidence")
    op.drop_table_comment(
        "schematic_renders",
        existing_comment="A replaceable preview artifact keyed by the complete rendering recipe.",
        schema=None,
    )
