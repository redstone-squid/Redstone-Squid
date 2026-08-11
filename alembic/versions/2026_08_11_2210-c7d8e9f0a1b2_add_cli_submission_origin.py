"""Add CLI submission provenance.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-08-11 22:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow server-derived CLI provenance on synchronized drafts."""
    op.drop_constraint("submission_drafts_origin_check", "submission_drafts", type_="check")
    op.create_check_constraint(
        "submission_drafts_origin_check",
        "submission_drafts",
        "origin IN ('discord', 'web', 'cli', 'paper', 'fabric')",
    )


def downgrade() -> None:
    """Remove CLI provenance only when no CLI-owned drafts remain."""
    op.execute("LOCK TABLE submission_drafts IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM submission_drafts WHERE origin = 'cli') THEN
                RAISE EXCEPTION 'cannot downgrade while CLI submission drafts are retained';
            END IF;
        END;
        $$
        """
    )
    op.drop_constraint("submission_drafts_origin_check", "submission_drafts", type_="check")
    op.create_check_constraint(
        "submission_drafts_origin_check",
        "submission_drafts",
        "origin IN ('discord', 'web', 'paper', 'fabric')",
    )
