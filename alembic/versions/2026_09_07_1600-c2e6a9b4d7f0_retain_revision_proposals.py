"""Retain recalculation candidates and atomic approval receipts.

Revision ID: c2e6a9b4d7f0
Revises: b1d5f8a3c6e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c2e6a9b4d7f0"
down_revision: str | Sequence[str] | None = "b1d5f8a3c6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Separate inferred changes from authorization to apply them."""
    op.create_table(
        "submission_revision_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "requested_by_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False),
        sa.Column("build_id", sa.Integer(), sa.ForeignKey("builds.id", ondelete="RESTRICT")),
        sa.Column("expected_revision", sa.Integer()),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT")),
        sa.Column("applied_revision", sa.Integer()),
        sa.CheckConstraint(
            "(build_id IS NULL) = (expected_revision IS NULL)", name="submission_revision_proposals_target_check"
        ),
        sa.CheckConstraint(
            "(approved_by_account_id IS NULL) = (applied_revision IS NULL)",
            name="submission_revision_proposals_receipt_check",
        ),
        sa.CheckConstraint(
            "expected_revision IS NULL OR expected_revision >= 0", name="submission_revision_proposals_revision_check"
        ),
        comment="A retained inference candidate, review snapshot, and atomic build revision receipt.",
    )
    for suffix, column in (
        ("run", "run_id"),
        ("owner", "owner_account_id"),
        ("actor", "requested_by_account_id"),
        ("approver", "approved_by_account_id"),
        ("build", "build_id"),
        ("source", "source_message_id"),
        ("expiry", "expires_at"),
    ):
        op.create_index(f"submission_revision_proposals_{suffix}_idx", "submission_revision_proposals", [column])


def downgrade() -> None:
    """Preserve retained candidates and approval history rather than silently dropping them."""
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM submission_revision_proposals)")):
        message = "Cannot downgrade while revision proposals are retained."
        raise RuntimeError(message)
    op.drop_table("submission_revision_proposals")
