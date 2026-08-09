"""Make schematic render projection durable.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-10 19:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Queue every primary schematic and retain its published object key."""
    op.add_column("schematic_renders", sa.Column("object_key", sa.Text(), nullable=True))
    op.create_table(
        "schematic_render_queue",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "enqueued_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dead_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["builds.id"],
            name="schematic_render_queue_build_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("build_id", name="schematic_render_queue_pkey"),
        comment="A durable request to render and publish one build's primary schematic.",
    )
    op.create_index(
        "schematic_render_queue_ready_idx",
        "schematic_render_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("claimed_at IS NULL AND dead_at IS NULL"),
    )
    op.execute(
        """
        INSERT INTO schematic_render_queue (build_id)
        SELECT build_id
        FROM build_schematics
        WHERE is_primary
        ON CONFLICT (build_id) DO NOTHING
        """
    )


def downgrade() -> None:
    """Remove render projection intents and private-object registration."""
    op.drop_index("schematic_render_queue_ready_idx", table_name="schematic_render_queue")
    op.drop_table("schematic_render_queue")
    op.drop_column("schematic_renders", "object_key")
