"""Preserve unchanged record publications and track successful checks."""

import sqlalchemy as sa

from alembic import op

revision: str = "a1c5e9f3b728"
down_revision: str | None = "f9a3b5c1d746"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("record_computation_runs", sa.Column("publication_fingerprint", sa.Text(), nullable=True))
    op.add_column("record_computation_runs", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE record_computation_runs SET last_checked_at = completed_at WHERE status = 'completed'")


def downgrade() -> None:
    op.drop_column("record_computation_runs", "last_checked_at")
    op.drop_column("record_computation_runs", "publication_fingerprint")
