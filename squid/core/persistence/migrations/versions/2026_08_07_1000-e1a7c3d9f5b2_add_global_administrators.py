"""Add global administrators and retire configured staff roles.

Revision ID: e1a7c3d9f5b2
Revises: 4c9e7a2b1d63
Create Date: 2026-08-07 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1a7c3d9f5b2"
down_revision: str | Sequence[str] | None = "4c9e7a2b1d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create bot-wide administrator grants and remove guild staff-role configuration."""
    op.create_table(
        "global_administrators",
        sa.Column("discord_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by_discord_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("discord_id"),
        comment="An active bot-wide administrator grant.",
    )
    op.drop_column("server_settings", "staff_roles_ids")


def downgrade() -> None:
    """Remove global administrator grants and restore an empty staff-role setting."""
    op.add_column("server_settings", sa.Column("staff_roles_ids", sa.ARRAY(sa.BigInteger()), nullable=True))
    op.drop_table("global_administrators")
