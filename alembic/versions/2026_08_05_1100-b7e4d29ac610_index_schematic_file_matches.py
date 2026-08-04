"""index schematic file matches

Revision ID: b7e4d29ac610
Revises: f3a9c25b7e41
Create Date: 2026-08-05 11:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7e4d29ac610"
down_revision: str | Sequence[str] | None = "f3a9c25b7e41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make reverse lookups from a content digest index-backed."""
    op.create_index("build_schematics_file_sha256_idx", "build_schematics", ["file_sha256"])


def downgrade() -> None:
    """Remove the reverse content-digest lookup index."""
    op.drop_index("build_schematics_file_sha256_idx", table_name="build_schematics")
