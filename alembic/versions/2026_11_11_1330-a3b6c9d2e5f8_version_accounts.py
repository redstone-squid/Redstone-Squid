"""Version the account aggregate."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3b6c9d2e5f8"
down_revision: str | Sequence[str] | None = "f2a5b8c1d4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")))
    op.create_check_constraint("accounts_revision_positive", "accounts", "revision > 0")


def downgrade() -> None:
    op.drop_constraint("accounts_revision_positive", "accounts", type_="check")
    op.drop_column("accounts", "revision")
