"""Add the transport-neutral domain-event consumer.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-10 11:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Register core work and backfill idempotent vote-outcome deliveries."""
    op.execute("INSERT INTO domain_event_consumers (name) VALUES ('core')")
    op.execute(
        """
        INSERT INTO domain_event_deliveries (event_id, consumer)
        SELECT id, 'core'
        FROM domain_events
        WHERE event_type = 'vote_session.closed'
        ON CONFLICT (event_id, consumer) DO NOTHING
        """
    )


def downgrade() -> None:
    """Remove the core consumer and its cascading deliveries."""
    op.execute("DELETE FROM domain_event_consumers WHERE name = 'core'")
