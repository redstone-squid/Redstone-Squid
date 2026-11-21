"""Register the build embedding event consumer."""

from alembic import op

revision: str = "d6b8f4310ae2"
down_revision: str | None = "c74e23b1f905"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("INSERT INTO domain_event_consumers (name) VALUES ('build_embeddings')")


def downgrade() -> None:
    op.execute("DELETE FROM domain_event_consumers WHERE name = 'build_embeddings'")
