"""Retain withdrawn proposal identities for session redo."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8a1b4c7d0e3"
down_revision: str | Sequence[str] | None = "e7f0a3b6c9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tag_definitions", sa.Column("withdrawn", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tag_definitions", "withdrawn")
