"""Version the runtime settings aggregate."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f4a7b0c3d6"
down_revision: str | Sequence[str] | None = "d0e3f6a9b2c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_settings", sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1"))
    )
    op.create_check_constraint("runtime_settings_revision_positive", "runtime_settings", "revision > 0")


def downgrade() -> None:
    op.drop_constraint("runtime_settings_revision_positive", "runtime_settings", type_="check")
    op.drop_column("runtime_settings", "revision")
