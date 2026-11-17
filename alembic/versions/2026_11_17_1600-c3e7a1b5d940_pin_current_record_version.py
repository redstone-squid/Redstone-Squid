"""Store the shared current record version independently from publications."""

import sqlalchemy as sa

from alembic import op

revision: str = "c3e7a1b5d940"
down_revision: str | None = "b2d6f0a4c839"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "record_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False, autoincrement=False),
        sa.Column("current_version_id", sa.SmallInteger(), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["current_version_id"], ["versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("id = 1", name="record_settings_singleton_check"),
        sa.CheckConstraint("revision >= 0", name="record_settings_revision_check"),
        comment="The shared current Minecraft version, independent of published runs.",
    )
    op.execute("""
        INSERT INTO record_settings (id, current_version_id)
        SELECT 1, (
            SELECT version_id FROM record_computation_runs
            WHERE is_active AND version_id IS NOT NULL
            ORDER BY started_at DESC, id DESC LIMIT 1
        )
    """)


def downgrade() -> None:
    op.drop_table("record_settings")
