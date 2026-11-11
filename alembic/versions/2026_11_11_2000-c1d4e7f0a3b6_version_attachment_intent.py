"""Separate versioned attachment intent from asynchronous source registration."""

from alembic import op

revision = "c1d4e7f0a3b6"
down_revision = "b0c3d6e9f2a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE submission_supplied_attachments ADD COLUMN history_managed boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.drop_column("submission_supplied_attachments", "history_managed")
