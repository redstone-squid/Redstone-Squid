"""Drop inline schematic payload storage.

Revision ID: a1b2c3d4e5f7
Revises: f0a1b2c3d4e5
Create Date: 2026-08-15 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Require object storage and discard legacy inline payloads."""
    op.drop_constraint("schematic_files_has_storage_location", "schematic_files", type_="check")
    op.drop_index("schematic_files_storage_state_idx", table_name="schematic_files")
    op.drop_constraint("schematic_files_storage_state_check", "schematic_files", type_="check")
    op.drop_column("schematic_files", "data")
    op.drop_column("schematic_files", "verified_at")
    op.drop_column("schematic_files", "storage_state")
    op.alter_column("schematic_files", "object_key", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    """Restore the legacy schema without reconstructing discarded payloads."""
    op.alter_column("schematic_files", "object_key", existing_type=sa.Text(), nullable=True)
    op.add_column(
        "schematic_files",
        sa.Column("storage_state", sa.Text(), server_default=sa.text("'ready'"), nullable=False),
    )
    op.add_column("schematic_files", sa.Column("verified_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("schematic_files", sa.Column("data", sa.LargeBinary(), nullable=True))
    op.create_check_constraint(
        "schematic_files_storage_state_check",
        "schematic_files",
        "storage_state IN ('pending', 'verified', 'ready')",
    )
    op.create_check_constraint(
        "schematic_files_has_storage_location",
        "schematic_files",
        "data IS NOT NULL OR object_key IS NOT NULL",
    )
    op.create_index(
        "schematic_files_storage_state_idx",
        "schematic_files",
        ["storage_state", "created_at"],
    )
