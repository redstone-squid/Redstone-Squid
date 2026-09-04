"""Retire finalization result metadata writes safely.

Revision ID: c1d7e0f5a8b2
Revises: b0c6d9e4f7a1
Create Date: 2026-08-31 11:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1d7e0f5a8b2"
down_revision: str | Sequence[str] | None = "b0c6d9e4f7a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Expand legacy columns before a later deployment drains their readers."""
    op.drop_constraint(
        "submission_finalization_results_target_key_check",
        "submission_finalization_results",
        type_="check",
    )
    op.alter_column("submission_finalization_results", "target_key", nullable=True)
    op.alter_column("submission_finalization_results", "provenance", nullable=True)
    op.create_check_constraint(
        "submission_finalization_results_target_key_check",
        "submission_finalization_results",
        "target_key IS NULL OR target_key ~ '^[a-z][a-z0-9_]{0,63}$'",
    )


def downgrade() -> None:
    """Reconstruct legacy metadata before restoring its required shape."""
    op.execute(
        """
        UPDATE submission_finalization_results AS result
        SET
            target_key = COALESCE(result.target_key, 'postgres_builds'),
            provenance = COALESCE(
                result.provenance,
                jsonb_build_object('source_draft_id', job.draft_id::text)
            )
        FROM submission_finalization_jobs AS job
        WHERE result.job_id = job.id
          AND (result.target_key IS NULL OR result.provenance IS NULL)
        """
    )
    op.drop_constraint(
        "submission_finalization_results_target_key_check",
        "submission_finalization_results",
        type_="check",
    )
    op.alter_column("submission_finalization_results", "provenance", nullable=False)
    op.alter_column("submission_finalization_results", "target_key", nullable=False)
    op.create_check_constraint(
        "submission_finalization_results_target_key_check",
        "submission_finalization_results",
        "target_key ~ '^[a-z][a-z0-9_]{0,63}$'",
    )
