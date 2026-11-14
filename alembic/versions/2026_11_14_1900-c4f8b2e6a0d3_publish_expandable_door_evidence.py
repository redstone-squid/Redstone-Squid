"""Retain concrete expandable-door evidence on ordinary record results.

Revision ID: c4f8b2e6a0d3
Revises: b3e7a1d5c9f2
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4f8b2e6a0d3"
down_revision: str | None = "b3e7a1d5c9f2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("title_results", sa.Column("door_evidence", postgresql.JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint(
        "title_results_door_evidence_check",
        "title_results",
        "door_evidence IS NULL OR (jsonb_typeof(door_evidence) = 'object' "
        "AND door_evidence->'schema_version' = '1'::jsonb) IS TRUE",
    )
    op.execute("""
        INSERT INTO record_recompute_queue (scope_key, build_kind, reasons)
        VALUES ('door', 'door', '["expandable_door_integration"]'::jsonb)
        ON CONFLICT (scope_key) DO UPDATE SET
            reasons = record_recompute_queue.reasons || EXCLUDED.reasons,
            enqueued_at = now(), available_at = now(),
            attempts = 0, claimed_at = NULL, claim_token = NULL, last_error = NULL
    """)


def downgrade() -> None:
    op.drop_constraint("title_results_door_evidence_check", "title_results", type_="check")
    op.drop_column("title_results", "door_evidence")
