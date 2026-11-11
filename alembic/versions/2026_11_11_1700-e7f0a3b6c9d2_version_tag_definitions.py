"""Version tag definitions together with their aliases."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7f0a3b6c9d2"
down_revision: str | Sequence[str] | None = "d6e9f2a5b8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tag_definitions", sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1"))
    )
    op.create_check_constraint("tag_definitions_revision_positive", "tag_definitions", "revision > 0")


def downgrade() -> None:
    op.drop_constraint("tag_definitions_revision_positive", "tag_definitions", type_="check")
    op.drop_column("tag_definitions", "revision")
