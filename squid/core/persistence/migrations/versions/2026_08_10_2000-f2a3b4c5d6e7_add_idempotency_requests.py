"""Add durable API idempotency requests.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-10 20:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store one completed mutation response per caller key for 24 hours."""
    op.create_table(
        "idempotency_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'in_progress'"), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_body", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('in_progress', 'completed')", name="idempotency_requests_state_check"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("principal", "idempotency_key", name="idempotency_requests_principal_key"),
        comment="One caller-scoped mutation reservation and its completed HTTP response.",
    )
    op.create_index("idempotency_requests_expires_at_idx", "idempotency_requests", ["expires_at"])


def downgrade() -> None:
    """Remove retained mutation responses and pending reservations."""
    op.drop_index("idempotency_requests_expires_at_idx", table_name="idempotency_requests")
    op.drop_table("idempotency_requests")
