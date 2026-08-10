"""Encrypt retained idempotency response bodies.

Revision ID: e3f4a5b6c7d8
Revises: d2f3a4b5c6d7
Create Date: 2026-08-11 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Purge plaintext replay state and require authenticated ciphertext fields."""
    op.execute(sa.text("DELETE FROM idempotency_requests"))
    op.alter_column(
        "idempotency_requests",
        "response_body",
        new_column_name="response_body_ciphertext",
        existing_type=sa.LargeBinary(),
        existing_nullable=True,
    )
    op.add_column("idempotency_requests", sa.Column("response_body_key_id", sa.Text(), nullable=True))
    op.add_column("idempotency_requests", sa.Column("response_body_nonce", sa.LargeBinary(), nullable=True))
    op.create_check_constraint(
        "idempotency_requests_response_state_check",
        "idempotency_requests",
        "(state = 'in_progress' AND response_status IS NULL AND response_headers IS NULL "
        "AND response_body_ciphertext IS NULL AND response_body_key_id IS NULL AND response_body_nonce IS NULL "
        "AND completed_at IS NULL) OR (state = 'completed' AND response_status IS NOT NULL "
        "AND response_headers IS NOT NULL AND response_body_ciphertext IS NOT NULL "
        "AND response_body_key_id IS NOT NULL AND response_body_nonce IS NOT NULL AND completed_at IS NOT NULL)",
    )


def downgrade() -> None:
    """Remove encrypted replay state before restoring the legacy column shape."""
    op.execute(sa.text("DELETE FROM idempotency_requests"))
    op.drop_constraint(
        "idempotency_requests_response_state_check",
        "idempotency_requests",
        type_="check",
    )
    op.drop_column("idempotency_requests", "response_body_nonce")
    op.drop_column("idempotency_requests", "response_body_key_id")
    op.alter_column(
        "idempotency_requests",
        "response_body_ciphertext",
        new_column_name="response_body",
        existing_type=sa.LargeBinary(),
        existing_nullable=True,
    )
