"""add schematic renders

Revision ID: c8d5e7f1a2b3
Revises: b7e4d29ac610
Create Date: 2026-08-05 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d5e7f1a2b3"
down_revision: str | Sequence[str] | None = "b7e4d29ac610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create recipe-keyed storage for replaceable rendered previews."""
    op.create_table(
        "schematic_renders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("build_schematic_id", sa.BigInteger(), nullable=False),
        sa.Column("recipe_hash", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("width > 0 AND height > 0 AND byte_size > 0", name="schematic_renders_sizes_positive"),
        sa.ForeignKeyConstraint(
            ["build_schematic_id"],
            ["build_schematics.id"],
            name="schematic_renders_build_schematic_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="schematic_renders_pkey"),
        sa.UniqueConstraint(
            "build_schematic_id",
            "recipe_hash",
            name="schematic_renders_schematic_recipe_key",
        ),
    )


def downgrade() -> None:
    """Drop rendered preview storage."""
    op.drop_table("schematic_renders")
