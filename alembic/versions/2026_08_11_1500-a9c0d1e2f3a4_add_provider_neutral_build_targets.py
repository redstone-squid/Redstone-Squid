"""Add provider-neutral synchronized build targets.

Revision ID: a9c0d1e2f3a4
Revises: f8b9c0d1e2f3
Create Date: 2026-08-11 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a9c0d1e2f3a4"
down_revision: str | Sequence[str] | None = "f8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store synchronized-draft identity and the remaining manifest category."""
    op.add_column("builds", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column(
        "builds",
        sa.Column("source_submission_draft_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "builds_display_name_valid",
        "builds",
        "display_name IS NULL OR (display_name = btrim(display_name) AND display_name <> '' "
        "AND char_length(display_name) <= 120)",
    )
    op.create_unique_constraint(
        "builds_source_submission_draft_id_key",
        "builds",
        ["source_submission_draft_id"],
    )
    op.create_table(
        "other_builds",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["builds.id"],
            name="other_builds_build_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("build_id"),
    )


def downgrade() -> None:
    """Remove synchronized build targets only while none have been used."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM builds
                WHERE display_name IS NOT NULL OR source_submission_draft_id IS NOT NULL OR category = 'Other'
            ) THEN
                RAISE EXCEPTION 'cannot downgrade after synchronized build targets are used';
            END IF;
        END;
        $$
        """
    )
    op.drop_table("other_builds")
    op.drop_constraint("builds_source_submission_draft_id_key", "builds", type_="unique")
    op.drop_constraint("builds_display_name_valid", "builds", type_="check")
    op.drop_column("builds", "source_submission_draft_id")
    op.drop_column("builds", "display_name")
