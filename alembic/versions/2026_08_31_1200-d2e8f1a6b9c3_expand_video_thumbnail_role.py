"""Expand durable media roles for video thumbnails.

Revision ID: d2e8f1a6b9c3
Revises: c1d7e0f5a8b2
Create Date: 2026-08-31 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2e8f1a6b9c3"
down_revision: str | Sequence[str] | None = "c1d7e0f5a8b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow new thumbnail writes while older workers still write poster rows."""
    op.drop_constraint("media_artifacts_role_check", "media_artifacts", type_="check")
    op.create_check_constraint(
        "media_artifacts_role_check",
        "media_artifacts",
        "role IN ('output', 'poster', 'video_thumbnail', 'report')",
    )


def downgrade() -> None:
    """Fold new thumbnail rows into the legacy value before restoring its check."""
    op.execute("UPDATE media_artifacts SET role = 'poster' WHERE role = 'video_thumbnail'")
    op.drop_constraint("media_artifacts_role_check", "media_artifacts", type_="check")
    op.create_check_constraint(
        "media_artifacts_role_check",
        "media_artifacts",
        "role IN ('output', 'poster', 'report')",
    )
