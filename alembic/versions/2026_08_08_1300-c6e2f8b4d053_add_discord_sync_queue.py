"""Add durable Discord reconciliation queue.

Revision ID: c6e2f8b4d053
Revises: b5d1e7a3c942
Create Date: 2026-08-08 13:00:00+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

revision: str = "c6e2f8b4d053"
down_revision: str | Sequence[str] | None = "b5d1e7a3c942"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {"enqueue_discord_sync"}
_TRIGGER_NAMES = {
    "build_creators_enqueue_discord_sync",
    "build_links_enqueue_discord_sync",
    "build_restrictions_enqueue_discord_sync",
    "build_tag_assignments_enqueue_discord_sync",
    "build_types_enqueue_discord_sync",
    "build_versions_enqueue_discord_sync",
    "builds_enqueue_discord_sync",
    "door_timing_variants_enqueue_discord_sync",
    "doors_enqueue_discord_sync",
    "extender_timing_variants_enqueue_discord_sync",
    "extenders_enqueue_discord_sync",
    "vote_sessions_enqueue_discord_sync",
    "votes_enqueue_discord_sync",
}


def upgrade() -> None:
    """Create the queue and install mutation triggers."""
    op.create_table(
        "discord_sync_queue",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), server_default=sa.text("'refresh'"), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("resource_kind IN ('build', 'vote_session')", name="discord_sync_queue_resource_kind_check"),
        sa.CheckConstraint("action IN ('refresh', 'delete')", name="discord_sync_queue_action_check"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_kind", "source_key", name="discord_sync_queue_resource_key"),
        comment="A coalesced request to refresh one Discord-rendered resource.",
    )
    op.create_index(
        "discord_sync_queue_ready_idx",
        "discord_sync_queue",
        ["enqueued_at"],
        unique=False,
        postgresql_where=sa.text("claimed_at IS NULL"),
    )
    for entity in _selected_entities(PGFunction, _FUNCTION_NAMES):
        op.execute(entity.to_sql_statement_create())
    for entity in _selected_entities(PGTrigger, _TRIGGER_NAMES):
        op.execute(entity.to_sql_statement_create())


def downgrade() -> None:
    """Remove reconciliation triggers, function, and queue."""
    # Later taxonomy migrations can reconstruct these tables while downgrading.
    # Keep the historical downgrade independent of the mutable entity registry.
    op.execute("DROP TRIGGER IF EXISTS build_restrictions_enqueue_discord_sync ON public.build_restrictions")
    op.execute("DROP TRIGGER IF EXISTS build_types_enqueue_discord_sync ON public.build_types")
    for entity in reversed(_selected_entities(PGTrigger, _TRIGGER_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    for entity in reversed(_selected_entities(PGFunction, _FUNCTION_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    op.drop_index("discord_sync_queue_ready_idx", table_name="discord_sync_queue")
    op.drop_table("discord_sync_queue")


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in ALEMBIC_UTIL_ENTITIES
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]
