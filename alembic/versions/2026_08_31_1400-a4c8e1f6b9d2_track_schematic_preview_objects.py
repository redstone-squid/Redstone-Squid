"""Track generated schematic preview object ownership.

Revision ID: a4c8e1f6b9d2
Revises: e3f9a2b7c0d4
Create Date: 2026-08-31 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4c8e1f6b9d2"
down_revision: str | Sequence[str] | None = "e3f9a2b7c0d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Register existing preview objects before enforcing render references."""
    op.create_table(
        "schematic_preview_objects",
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("ready_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="schematic_preview_objects_size_positive"),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="schematic_preview_objects_sha256_check",
        ),
        sa.PrimaryKeyConstraint("object_key", name="schematic_preview_objects_pkey"),
    )
    op.create_index(
        "schematic_preview_objects_cleanup_idx",
        "schematic_preview_objects",
        ["last_seen_at"],
    )
    op.execute(
        """
        INSERT INTO schematic_preview_objects (object_key, byte_size, ready_at)
        SELECT object_key, max(byte_size), now()
        FROM schematic_renders
        WHERE object_key IS NOT NULL
        GROUP BY object_key
        """
    )
    op.create_foreign_key(
        "schematic_renders_object_key_fkey",
        "schematic_renders",
        "schematic_preview_objects",
        ["object_key"],
        ["object_key"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove preview-object lifecycle state without rewriting stored objects."""
    op.drop_constraint("schematic_renders_object_key_fkey", "schematic_renders", type_="foreignkey")
    op.drop_index("schematic_preview_objects_cleanup_idx", table_name="schematic_preview_objects")
    op.drop_table("schematic_preview_objects")
