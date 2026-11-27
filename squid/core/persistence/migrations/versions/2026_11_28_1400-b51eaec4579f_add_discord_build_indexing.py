"""Make automatic Discord build indexing an explicit account preference."""

from alembic import op

revision = "b51eaec4579f"
down_revision = "a4e8b2d7c615"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE accounts ADD COLUMN discord_build_indexing_enabled boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.drop_column("accounts", "discord_build_indexing_enabled")
