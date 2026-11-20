"""Move schematic payload metadata to object storage.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-10 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add staged object metadata while retaining legacy payloads for online backfill."""
    op.create_table_comment(
        "schematic_files",
        "Relational metadata for a content-addressed schematic artifact.",
        existing_comment=(
            "Schematic bytes, content-addressed by SHA-256.\n\n"
            "Held in Postgres rather than an object host because these bytes are re-read on every\n"
            "re-render, diff, and duplicate check; the alternative is an HTTP fetch of an\n"
            "attacker-influenced URL on each one. Content addressing also means a byte-identical\n"
            "resubmission is recognised before any analysis runs."
        ),
    )
    op.add_column("schematic_files", sa.Column("object_key", sa.Text(), nullable=True))
    op.add_column(
        "schematic_files",
        sa.Column("storage_state", sa.Text(), server_default=sa.text("'ready'"), nullable=False),
    )
    op.add_column("schematic_files", sa.Column("verified_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.alter_column("schematic_files", "data", existing_type=sa.LargeBinary(), nullable=True)
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


def downgrade() -> None:
    """Restore inline-only storage when every row still carries legacy data."""
    connection = op.get_bind()
    missing_inline = connection.scalar(sa.text("SELECT count(*) FROM schematic_files WHERE data IS NULL"))
    if missing_inline:
        msg = "Cannot downgrade object storage after inline schematic payloads have been removed."
        raise RuntimeError(msg)
    op.create_table_comment(
        "schematic_files",
        (
            "Schematic bytes, content-addressed by SHA-256.\n\n"
            "Held in Postgres rather than an object host because these bytes are re-read on every\n"
            "re-render, diff, and duplicate check; the alternative is an HTTP fetch of an\n"
            "attacker-influenced URL on each one. Content addressing also means a byte-identical\n"
            "resubmission is recognised before any analysis runs."
        ),
        existing_comment="Relational metadata for a content-addressed schematic artifact.",
    )
    op.drop_index("schematic_files_storage_state_idx", table_name="schematic_files")
    op.drop_constraint("schematic_files_has_storage_location", "schematic_files", type_="check")
    op.drop_constraint("schematic_files_storage_state_check", "schematic_files", type_="check")
    op.alter_column("schematic_files", "data", existing_type=sa.LargeBinary(), nullable=False)
    op.drop_column("schematic_files", "verified_at")
    op.drop_column("schematic_files", "storage_state")
    op.drop_column("schematic_files", "object_key")
