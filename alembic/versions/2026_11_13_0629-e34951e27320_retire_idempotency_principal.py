"""Retire the idempotency principal compatibility column.

Drain API processes before upgrading: older binaries still write principal.
Replace SQUID_RATE_LIMIT_PRINCIPAL_REQUESTS with SQUID_RATE_LIMIT_CALLER_REQUESTS
in deployment configuration before starting the new release. Downgrade before
restarting older binaries; it restores principal from caller for every reservation.

Revision ID: e34951e27320
Revises: d23840d16219
"""

import sqlalchemy as sa

from alembic import op

revision: str = "e34951e27320"
down_revision: str = "d23840d16219"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("idempotency_requests_principal_key", "idempotency_requests", type_="unique")
    op.drop_column("idempotency_requests", "principal")


def downgrade() -> None:
    op.add_column(
        "idempotency_requests",
        sa.Column(
            "principal",
            sa.Text(),
            nullable=True,
            comment="Legacy copy of caller, still written by the repository for the retained database column.",
        ),
    )
    op.execute("UPDATE idempotency_requests SET principal = caller")
    op.alter_column("idempotency_requests", "principal", nullable=False)
    op.create_unique_constraint(
        "idempotency_requests_principal_key", "idempotency_requests", ["principal", "idempotency_key"]
    )
