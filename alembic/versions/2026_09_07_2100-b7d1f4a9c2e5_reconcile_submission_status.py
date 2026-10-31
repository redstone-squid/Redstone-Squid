"""Reconcile public inference status and private draft projections.

Revision ID: b7d1f4a9c2e5
Revises: a6c0e3f8b1d4
"""

from collections.abc import Sequence
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "b7d1f4a9c2e5"
down_revision: str = "a6c0e3f8b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMES = {
    "enqueue_submission_status",
    "submission_runs_enqueue_status",
    "submission_drafts_enqueue_status",
    "submission_intake_enqueue_status",
}


def upgrade() -> None:
    op.add_column("submission_drafts", sa.Column("inference_run_id", postgresql.UUID(as_uuid=True)))
    op.create_index("ix_submission_drafts_inference_run_id", "submission_drafts", ["inference_run_id"])
    for table in ("discord_posts", "discord_sync_queue"):
        op.drop_constraint(f"{table}_resource_kind_check", table)
        op.create_check_constraint(
            f"{table}_resource_kind_check",
            table,
            "resource_kind IN ('build', 'vote_session', 'starboard_entry', 'inference_run', 'submission_draft')",
        )
    op.drop_constraint("discord_posts_surface_check", "discord_posts")
    op.create_check_constraint(
        "discord_posts_surface_check",
        "discord_posts",
        "surface IN ('build_card', 'build_review', 'vote_card', 'starboard_entry', 'submission_status')",
    )
    for entity in alembic_util_entities():
        if entity.signature.partition("(")[0] in _NAMES:
            op.execute(entity.to_sql_statement_create())
    connection = op.get_bind()
    for run_id, count in connection.execute(
        sa.text(
            "SELECT id, jsonb_array_length(candidates) FROM submission_inference_runs WHERE inputs->>'purpose' = 'submission'"
        )
    ):
        for index in range(count):
            connection.execute(
                sa.text("UPDATE submission_drafts SET inference_run_id = :run WHERE id = :draft"),
                {"run": run_id, "draft": uuid5(UUID(str(run_id)), f"candidate:{index}")},
            )
    connection.execute(
        sa.text("UPDATE submission_inference_runs SET state = state WHERE inputs->>'purpose' = 'submission'")
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM discord_posts WHERE resource_kind IN ('inference_run', 'submission_draft'))"
        )
    ):
        message = "Cannot orphan retained submission status posts."
        raise RuntimeError(message)
    for entity in reversed(alembic_util_entities()):
        if entity.signature.partition("(")[0] in _NAMES:
            op.execute(entity.to_sql_statement_drop())
    op.execute("DELETE FROM discord_sync_queue WHERE resource_kind IN ('inference_run', 'submission_draft')")
    for table in ("discord_posts", "discord_sync_queue"):
        op.drop_constraint(f"{table}_resource_kind_check", table)
        op.create_check_constraint(
            f"{table}_resource_kind_check", table, "resource_kind IN ('build', 'vote_session', 'starboard_entry')"
        )
    op.drop_constraint("discord_posts_surface_check", "discord_posts")
    op.create_check_constraint(
        "discord_posts_surface_check",
        "discord_posts",
        "surface IN ('build_card', 'build_review', 'vote_card', 'starboard_entry')",
    )
    op.drop_index("ix_submission_drafts_inference_run_id", table_name="submission_drafts")
    op.drop_column("submission_drafts", "inference_run_id")
