"""Retain immutable submission attempt inputs.

Revision ID: c8e2a5b0d3f6
Revises: b7d1f4a9c2e5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "c8e2a5b0d3f6"
down_revision: str | Sequence[str] | None = "b7d1f4a9c2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMES = {
    "reject_submission_finalization_input_update",
    "submission_finalization_inputs_immutable",
}


def upgrade() -> None:
    """Separate the accepted request from mutable worker execution state."""
    op.create_table(
        "submission_finalization_inputs",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submission_finalization_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="submission_finalization_inputs_payload_sha256_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="submission_finalization_inputs_payload_object_check",
        ),
        comment="Original normalized request retained independently of mutable finalization execution state.",
    )
    op.execute("""
        INSERT INTO submission_finalization_inputs (job_id, payload, payload_sha256, created_at)
        SELECT id, payload, payload_sha256, created_at
        FROM submission_finalization_jobs
        WHERE payload IS NOT NULL AND payload_sha256 IS NOT NULL
    """)
    for entity in alembic_util_entities():
        if entity.signature.partition("(")[0] in _NAMES:
            op.execute(entity.to_sql_statement_create())


def downgrade() -> None:
    """Remove only the redundant immutable copy; the executable payload remains retained."""
    for entity in reversed(alembic_util_entities()):
        if entity.signature.partition("(")[0] in _NAMES:
            op.execute(entity.to_sql_statement_drop())
    op.drop_table("submission_finalization_inputs")
