"""add server locale override

Revision ID: 9a1e4c7b3d62
Revises: 6f3d9c8a2b71
Create Date: 2026-07-31 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9a1e4c7b3d62"
down_revision: str | Sequence[str] | None = "6f3d9c8a2b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the admin-configurable per-server locale override."""
    op.add_column("server_settings", sa.Column("locale", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the per-server locale override."""
    op.drop_column("server_settings", "locale")
