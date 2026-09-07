"""Distinguish inferred intake from manual account capacity.

Revision ID: e8a2c5d0f3b6
Revises: d7f1b4c9e2a5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8a2c5d0f3b6"
down_revision: str | Sequence[str] | None = "d7f1b4c9e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Retain existing drafts in the manual pool and index inferred expiry."""
    op.add_column("submission_drafts", sa.Column("inferred", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_check_constraint(
        "submission_drafts_inference_origin_check", "submission_drafts", "NOT inferred OR origin = 'discord'"
    )
    op.create_index(
        "submission_drafts_inferred_expiry_idx",
        "submission_drafts",
        ["expires_at"],
        postgresql_where=sa.text("inferred"),
    )


def downgrade() -> None:
    """Preserve the provenance of retained inferred drafts."""
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM submission_drafts WHERE inferred)")):
        message = "Cannot downgrade while inferred drafts are retained."
        raise RuntimeError(message)
    op.drop_index("submission_drafts_inferred_expiry_idx", table_name="submission_drafts")
    op.drop_constraint("submission_drafts_inference_origin_check", "submission_drafts", type_="check")
    op.drop_column("submission_drafts", "inferred")
