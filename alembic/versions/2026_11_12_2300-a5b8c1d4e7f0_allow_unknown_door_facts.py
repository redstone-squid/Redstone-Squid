"""Allow unknown door opening dimensions and orientation.

Revision ID: a5b8c1d4e7f0
Revises: f4a7b0c3d6e9
"""

import sqlalchemy as sa

from alembic import op

revision = "a5b8c1d4e7f0"
down_revision = "f4a7b0c3d6e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("doors", "door_width", existing_type=sa.Integer(), nullable=True)
    op.alter_column("doors", "door_height", existing_type=sa.Integer(), nullable=True)
    op.alter_column("doors", "orientation", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # PostgreSQL refuses this downgrade if unknown facts remain; do not invent
    # values or delete incomplete builds to satisfy the old schema.
    op.alter_column("doors", "door_width", existing_type=sa.Integer(), nullable=False)
    op.alter_column("doors", "door_height", existing_type=sa.Integer(), nullable=False)
    op.alter_column("doors", "orientation", existing_type=sa.Text(), nullable=False)
