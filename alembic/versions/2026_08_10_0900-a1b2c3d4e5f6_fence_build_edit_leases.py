"""Fence build edit leases.

Revision ID: a1b2c3d4e5f6
Revises: e8a4b1d7c390
Create Date: 2026-08-10 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "e8a4b1d7c390"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ownership and expiry to the existing build-edit lease."""
    op.add_column("builds", sa.Column("lock_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("builds", sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True))
    # A lock cannot safely survive this migration because the old row contains no
    # owner token. Active edits are excluded by the coordinated deployment window.
    op.execute("UPDATE builds SET is_locked = false, locked_at = NULL WHERE is_locked")
    op.create_check_constraint(
        "builds_lock_lease_complete",
        "builds",
        "(is_locked AND lock_token IS NOT NULL AND lock_expires_at IS NOT NULL) "
        "OR (NOT is_locked AND lock_token IS NULL AND lock_expires_at IS NULL)",
    )


def downgrade() -> None:
    """Remove build lease ownership and expiry."""
    op.drop_constraint("builds_lock_lease_complete", "builds", type_="check")
    op.drop_column("builds", "lock_expires_at")
    op.drop_column("builds", "lock_token")
