"""Retain the actor independently of submission ownership.

Revision ID: a0c4e7f2b5d8
Revises: f9b3d6e1a4c7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a0c4e7f2b5d8"
down_revision: str | Sequence[str] | None = "f9b3d6e1a4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Attribute pre-staff submissions to their then-exclusive owner authority."""
    op.add_column("submission_drafts", sa.Column("submission_actor_account_id", sa.Integer(), nullable=True))
    op.add_column("submission_finalization_jobs", sa.Column("requested_by_account_id", sa.Integer(), nullable=True))
    op.create_index(
        "submission_drafts_actor_idx",
        "submission_drafts",
        ["submission_actor_account_id"],
        postgresql_where=sa.text("submission_actor_account_id IS NOT NULL"),
    )
    op.create_index(
        "submission_finalization_jobs_actor_idx",
        "submission_finalization_jobs",
        ["requested_by_account_id"],
        postgresql_where=sa.text("requested_by_account_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "submission_drafts_submission_actor_account_id_fkey",
        "submission_drafts",
        "accounts",
        ["submission_actor_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "submission_finalization_jobs_requested_by_account_id_fkey",
        "submission_finalization_jobs",
        "accounts",
        ["requested_by_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        "UPDATE submission_drafts SET submission_actor_account_id = owner_account_id WHERE preparation_retry_at IS NOT NULL"
    )
    op.execute("""
        UPDATE submission_finalization_jobs AS job SET requested_by_account_id = draft.owner_account_id
        FROM submission_drafts AS draft WHERE job.draft_id = draft.id
    """)


def downgrade() -> None:
    """Refuse to erase staff attribution that differs from ownership."""
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM submission_drafts WHERE submission_actor_account_id <> owner_account_id)
        OR EXISTS (SELECT 1 FROM submission_finalization_jobs AS job JOIN submission_drafts AS draft ON draft.id = job.draft_id
        WHERE job.requested_by_account_id <> draft.owner_account_id)
    """)
    ):
        message = "Cannot downgrade while staff submission attribution is retained."
        raise RuntimeError(message)
    op.drop_column("submission_finalization_jobs", "requested_by_account_id")
    op.drop_column("submission_drafts", "submission_actor_account_id")
