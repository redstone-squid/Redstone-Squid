"""Retain staff attribution for profile clears."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f6a9b2c5d8"
down_revision: str | Sequence[str] | None = "d2e5f8a1b4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IDs deliberately survive account deletion; removed profile content is never copied here.
    op.create_table(
        "profile_clear_audit",
        sa.Column("operation_id", sa.UUID(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("actor_account_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        comment="Retain staff attribution without retaining the removed profile content.",
    )


def downgrade() -> None:
    op.drop_table("profile_clear_audit")
