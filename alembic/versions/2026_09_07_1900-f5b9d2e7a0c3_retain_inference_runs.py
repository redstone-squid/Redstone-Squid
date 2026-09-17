"""Retain private inference inputs and stable candidates.

Revision ID: f5b9d2e7a0c3
Revises: e4a8c1d6f9b2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f5b9d2e7a0c3"
down_revision: str = "e4a8c1d6f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission_inference_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "state IN ('processing', 'failed', 'completed')", name="submission_inference_runs_state_check"
        ),
        comment="Exact private inference inputs and retained candidate facts under a renewable claim.",
    )
    op.create_index("ix_submission_inference_runs_owner_account_id", "submission_inference_runs", ["owner_account_id"])
    op.create_index("ix_submission_inference_runs_expires_at", "submission_inference_runs", ["expires_at"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM submission_inference_runs)")):
        message = "Cannot drop retained inference inputs and candidates."
        raise RuntimeError(message)
    op.drop_table("submission_inference_runs")
