"""Replace the retired sanitizer wait with schematic analysis states.

Revision ID: c9d2e5f8a1b4
Revises: b2c7d0e5f8a1
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c9d2e5f8a1b4"
down_revision = "b2c7d0e5f8a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("submission_schematic_sources", sa.Column("analysis", postgresql.JSONB(), nullable=True))
    op.add_column("submission_schematic_sources", sa.Column("last_error", sa.Text(), nullable=True))
    for suffix in ("state_check", "uploaded_check"):
        op.drop_constraint(f"submission_schematic_sources_{suffix}", "submission_schematic_sources")
    op.execute("UPDATE submission_schematic_sources SET state = 'pending' WHERE state = 'waiting_sanitizer'")
    op.create_check_constraint(
        "submission_schematic_sources_state_check",
        "submission_schematic_sources",
        "state IN ('uploading', 'pending', 'ready', 'rejected', 'failed', 'deleting', 'deleted')",
    )
    op.create_check_constraint(
        "submission_schematic_sources_uploaded_check",
        "submission_schematic_sources",
        "state NOT IN ('pending', 'ready', 'rejected') OR (sha256 IS NOT NULL AND byte_size IS NOT NULL)",
    )
    op.create_check_constraint(
        "submission_schematic_sources_ready_check",
        "submission_schematic_sources",
        "state <> 'ready' OR analysis IS NOT NULL",
    )
    # Earlier releases could leave preparation attention without scheduling another attempt.
    op.execute("""
        UPDATE submission_drafts SET preparation_retry_at = now()
        WHERE status = 'needs_attention'
          AND preparation_issues = '[{"field_id":"schematic","reason":"schematic_processing"}]'::jsonb
    """)


def downgrade() -> None:
    for suffix in ("state_check", "uploaded_check", "ready_check"):
        op.drop_constraint(f"submission_schematic_sources_{suffix}", "submission_schematic_sources")
    op.execute("""
        UPDATE submission_schematic_sources SET state = 'waiting_sanitizer'
        WHERE state IN ('pending', 'ready', 'rejected')
    """)
    op.create_check_constraint(
        "submission_schematic_sources_state_check",
        "submission_schematic_sources",
        "state IN ('uploading', 'waiting_sanitizer', 'failed', 'deleting', 'deleted')",
    )
    op.create_check_constraint(
        "submission_schematic_sources_uploaded_check",
        "submission_schematic_sources",
        "state <> 'waiting_sanitizer' OR (sha256 IS NOT NULL AND byte_size IS NOT NULL)",
    )
    op.drop_column("submission_schematic_sources", "last_error")
    op.drop_column("submission_schematic_sources", "analysis")
