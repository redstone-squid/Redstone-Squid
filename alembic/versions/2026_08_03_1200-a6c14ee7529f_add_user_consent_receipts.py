"""add user consent receipts

Revision ID: a6c14ee7529f
Revises: 3c8b1f0a6d24
Create Date: 2026-08-03 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6c14ee7529f"
down_revision: str | Sequence[str] | None = "3c8b1f0a6d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record the privacy notice version and time accepted by a user."""
    op.add_column("users", sa.Column("consent_version", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "users_consent_receipt_complete",
        "users",
        "(consent_version IS NULL) = (consented_at IS NULL)",
    )


def downgrade() -> None:
    """Remove user consent receipt metadata."""
    op.drop_constraint("users_consent_receipt_complete", "users", type_="check")
    op.drop_column("users", "consented_at")
    op.drop_column("users", "consent_version")
