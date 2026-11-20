"""Document why archived starboards keep their rows."""

from alembic import op

revision = "e3a6c9f2b5d8"
down_revision = "c1d4e7f0a3b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "COMMENT ON COLUMN starboards.archived IS "
        "'Hidden boards retain their ID and revision through creation undo and final deletion.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN starboards.archived IS NULL")
