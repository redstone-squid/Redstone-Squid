"""Retain finalization attempts instead of resetting their payloads.

Revision ID: c6e0a3b8d1f4
Revises: b5d9f2a7c0e3
Create Date: 2026-09-07 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c6e0a3b8d1f4"
down_revision: str | Sequence[str] | None = "b5d9f2a7c0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep existing identities/results and permit independently retained attempts."""
    op.add_column(
        "submission_finalization_jobs",
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_constraint("submission_finalization_jobs_draft_id_key", "submission_finalization_jobs", type_="unique")
    op.create_unique_constraint(
        "submission_finalization_jobs_draft_attempt_key",
        "submission_finalization_jobs",
        ["draft_id", "attempt_number"],
    )
    op.create_check_constraint(
        "submission_finalization_jobs_attempt_number_positive", "submission_finalization_jobs", "attempt_number > 0"
    )
    op.create_index(
        "submission_finalization_jobs_one_active_attempt",
        "submission_finalization_jobs",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )


def downgrade() -> None:
    """Refuse to discard retained history when restoring the single-job schema."""
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM submission_finalization_jobs GROUP BY draft_id HAVING count(*) > 1)")
    ):
        message = "Cannot downgrade while multiple finalization attempts exist; retain the history or restore a backup."
        raise RuntimeError(message)
    op.drop_index("submission_finalization_jobs_one_active_attempt", table_name="submission_finalization_jobs")
    op.drop_constraint(
        "submission_finalization_jobs_attempt_number_positive", "submission_finalization_jobs", type_="check"
    )
    op.drop_constraint("submission_finalization_jobs_draft_attempt_key", "submission_finalization_jobs", type_="unique")
    op.create_unique_constraint(
        "submission_finalization_jobs_draft_id_key", "submission_finalization_jobs", ["draft_id"]
    )
    op.drop_column("submission_finalization_jobs", "attempt_number")
