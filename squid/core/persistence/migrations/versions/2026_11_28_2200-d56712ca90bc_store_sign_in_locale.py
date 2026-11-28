"""Keep the sign-in page locale with each OAuth attempt."""

from alembic import op

revision = "d56712ca90bc"
down_revision = "1af9428ed50c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE oauth_states ADD COLUMN locale text")


def downgrade() -> None:
    op.drop_column("oauth_states", "locale")
