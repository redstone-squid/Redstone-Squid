"""Version the guild settings aggregate."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2a5b8c1d4e7"
down_revision: str | Sequence[str] | None = "e1f4a7b0c3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "server_settings", sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1"))
    )
    op.create_check_constraint("server_settings_revision_positive", "server_settings", "revision > 0")


def downgrade() -> None:
    op.drop_constraint("server_settings_revision_positive", "server_settings", type_="check")
    op.drop_column("server_settings", "revision")
