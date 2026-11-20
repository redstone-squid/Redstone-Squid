"""Retain starboard identities and revisions through undo and deletion."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5d8e1f4a7b0"
down_revision: str | Sequence[str] | None = "b4c7d0e3f6a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("starboards", sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")))
    op.add_column("starboards", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_check_constraint("starboards_revision_positive", "starboards", "revision > 0")
    op.drop_index("starboards_guild_name_key", table_name="starboards")
    op.create_index(
        "starboards_guild_name_key",
        "starboards",
        ["guild_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("NOT archived"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM starboards WHERE archived")
    op.drop_index("starboards_guild_name_key", table_name="starboards")
    op.create_index("starboards_guild_name_key", "starboards", ["guild_id", sa.text("lower(name)")], unique=True)
    op.drop_constraint("starboards_revision_positive", "starboards", type_="check")
    op.drop_column("starboards", "archived")
    op.drop_column("starboards", "revision")
