"""Gate schematic publication on rights and sanitization.

Revision ID: f8b9c0d1e2f3
Revises: e7a8b9c0d1e2
Create Date: 2026-08-11 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "e7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_MAX_BYTES = 16 * 1024 * 1024
_OLD_MAX_BYTES = 2 * 1024 * 1024


def upgrade() -> None:
    op.drop_constraint("schematic_files_size_bounded", "schematic_files", type_="check")
    op.create_check_constraint(
        "schematic_files_size_bounded",
        "schematic_files",
        f"byte_size > 0 AND byte_size <= {_NEW_MAX_BYTES}",
    )

    op.add_column(
        "build_schematics",
        sa.Column("visibility", sa.Text(), server_default=sa.text("'legacy_unverified'"), nullable=False),
    )
    op.add_column("build_schematics", sa.Column("license_code", sa.Text(), nullable=True))
    op.add_column(
        "build_schematics",
        sa.Column("rights_attested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("build_schematics", sa.Column("rights_attested_by_account_id", sa.Integer(), nullable=True))
    op.add_column("build_schematics", sa.Column("sanitized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("build_schematics", sa.Column("sanitizer_version", sa.Text(), nullable=True))
    op.add_column(
        "build_schematics",
        sa.Column("sanitization_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("build_schematics", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("build_schematics", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "build_schematics_rights_attested_by_account_id_fkey",
        "build_schematics",
        "accounts",
        ["rights_attested_by_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "build_schematics_visibility_check",
        "build_schematics",
        "visibility IN ('legacy_unverified', 'reviewer_only', 'public_download')",
    )
    op.create_check_constraint(
        "build_schematics_license_check",
        "build_schematics",
        "license_code IS NULL OR license_code IN ("
        "'cc0_1_0', 'cc_by_4_0', 'cc_by_sa_4_0', 'cc_by_nd_4_0', "
        "'cc_by_nc_4_0', 'cc_by_nc_sa_4_0', 'cc_by_nc_nd_4_0')",
    )
    op.create_check_constraint(
        "build_schematics_publication_complete",
        "build_schematics",
        "visibility <> 'public_download' OR (license_code IS NOT NULL AND rights_attested_at IS NOT NULL "
        "AND rights_attested_by_account_id IS NOT NULL)",
    )
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
        unique=False,
        postgresql_where=sa.text(
            "visibility = 'public_download' AND withdrawn_at IS NULL AND sanitized_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("build_schematics_public_download_idx", table_name="build_schematics")
    op.drop_constraint("build_schematics_sanitization_complete", "build_schematics", type_="check")
    op.drop_constraint("build_schematics_publication_complete", "build_schematics", type_="check")
    op.drop_constraint("build_schematics_license_check", "build_schematics", type_="check")
    op.drop_constraint("build_schematics_visibility_check", "build_schematics", type_="check")
    op.drop_constraint(
        "build_schematics_rights_attested_by_account_id_fkey",
        "build_schematics",
        type_="foreignkey",
    )
    op.drop_column("build_schematics", "withdrawn_at")
    op.drop_column("build_schematics", "published_at")
    op.drop_column("build_schematics", "sanitization_report")
    op.drop_column("build_schematics", "sanitizer_version")
    op.drop_column("build_schematics", "sanitized_at")
    op.drop_column("build_schematics", "rights_attested_by_account_id")
    op.drop_column("build_schematics", "rights_attested_at")
    op.drop_column("build_schematics", "license_code")
    op.drop_column("build_schematics", "visibility")

    op.drop_constraint("schematic_files_size_bounded", "schematic_files", type_="check")
    op.create_check_constraint(
        "schematic_files_size_bounded",
        "schematic_files",
        f"byte_size > 0 AND byte_size <= {_OLD_MAX_BYTES}",
    )
