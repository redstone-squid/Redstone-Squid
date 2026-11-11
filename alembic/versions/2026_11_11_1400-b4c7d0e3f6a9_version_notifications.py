"""Version notification settings and individual inbox items."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4c7d0e3f6a9"
down_revision: str | Sequence[str] | None = "a3b6c9d2e5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("notification_profiles", "notifications"):
        op.add_column(name, sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")))
        op.create_check_constraint(f"{name}_revision_positive", name, "revision > 0")


def downgrade() -> None:
    for name in ("notifications", "notification_profiles"):
        op.drop_constraint(f"{name}_revision_positive", name, type_="check")
        op.drop_column(name, "revision")
