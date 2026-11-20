"""Track successful range checks without replacing unchanged publications."""

import sqlalchemy as sa

from alembic import op

revision: str = "e5a9c3d7f162"
down_revision: str | None = "d4f8b2c6e051"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("door_range_registrations", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE door_range_registrations AS query
        SET last_checked_at = run.completed_at
        FROM door_range_runs AS run WHERE run.query_id = query.id AND run.is_active
    """)


def downgrade() -> None:
    op.drop_column("door_range_registrations", "last_checked_at")
