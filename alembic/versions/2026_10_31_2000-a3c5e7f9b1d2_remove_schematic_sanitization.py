"""Remove schematic sanitization state and its publication index predicate.

Revision ID: a3c5e7f9b1d2
Revises: d2f3a4b5c6d7
Create Date: 2026-10-31 20:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a3c5e7f9b1d2"
down_revision: str = "d2f3a4b5c6d7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("build_schematics_public_download_idx", table_name="build_schematics")
    op.drop_constraint("build_schematics_sanitization_complete", "build_schematics", type_="check")
    op.drop_column("build_schematics", "sanitized_at")
    op.drop_column("build_schematics", "sanitizer_version")
    op.drop_column("build_schematics", "sanitization_report")
    op.create_index(
        "build_schematics_public_download_idx",
        "build_schematics",
        ["build_id", "id"],
        postgresql_where=sa.text("visibility = 'public_download' AND withdrawn_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("build_schematics_public_download_idx", table_name="build_schematics")
    # Removed certificates cannot be reconstructed; the previous gate treats these as unprocessed.
    op.add_column("build_schematics", sa.Column("sanitized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("build_schematics", sa.Column("sanitizer_version", sa.Text(), nullable=True))
    op.add_column("build_schematics", sa.Column("sanitization_report", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "build_schematics_sanitization_complete",
        "build_schematics",
        "(sanitized_at IS NULL) = (sanitizer_version IS NULL) AND "
        "(sanitized_at IS NULL) = (sanitization_report IS NULL)",
    )
    op.create_index(
        "build_schematics_public_download_idx",
        "build_schematics",
        ["build_id", "id"],
        postgresql_where=sa.text(
            "visibility = 'public_download' AND withdrawn_at IS NULL AND sanitized_at IS NOT NULL"
        ),
    )
