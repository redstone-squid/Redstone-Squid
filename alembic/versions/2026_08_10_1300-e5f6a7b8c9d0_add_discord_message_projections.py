"""Add desired Discord message projection state.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-10 13:00:00+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {
    "bump_discord_sync_generation",
    "initialize_discord_message_projection",
    "project_discord_message_desired_state",
}
_TRIGGER_NAMES = {
    "discord_sync_queue_bump_generation",
    "discord_sync_queue_project_desired_state",
    "messages_initialize_discord_projection",
}


def upgrade() -> None:
    """Persist desired/applied generations and retain deletion targets."""
    op.add_column(
        "discord_sync_queue",
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column("messages", sa.Column("projection_resource_kind", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("projection_source_key", sa.Text(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("desired_action", sa.Text(), server_default=sa.text("'refresh'"), nullable=False),
    )
    op.add_column(
        "messages",
        sa.Column("desired_revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "messages",
        sa.Column("applied_revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.drop_constraint("public_messages_build_id_fkey", "messages", type_="foreignkey")
    op.create_foreign_key(
        "public_messages_build_id_fkey",
        "messages",
        "builds",
        ["build_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE messages m
        SET projection_resource_kind = CASE
                WHEN m.vote_session_id IS NOT NULL THEN 'vote_session'
                ELSE 'build'
            END,
            projection_source_key = COALESCE(m.vote_session_id, m.build_id)::text,
            desired_revision = COALESCE(q.generation, 1),
            applied_revision = COALESCE(q.generation, 1)
        FROM discord_sync_queue q
        WHERE m.purpose <> 'build_original_message'
          AND (m.vote_session_id IS NOT NULL OR m.build_id IS NOT NULL)
          AND q.resource_kind = CASE
                WHEN m.vote_session_id IS NOT NULL THEN 'vote_session'
                ELSE 'build'
            END
          AND q.source_key = COALESCE(m.vote_session_id, m.build_id)::text
        """
    )
    op.execute(
        """
        UPDATE messages m
        SET projection_resource_kind = CASE
                WHEN m.vote_session_id IS NOT NULL THEN 'vote_session'
                ELSE 'build'
            END,
            projection_source_key = COALESCE(m.vote_session_id, m.build_id)::text
        WHERE m.purpose <> 'build_original_message'
          AND (m.vote_session_id IS NOT NULL OR m.build_id IS NOT NULL)
          AND m.projection_resource_kind IS NULL
        """
    )
    op.create_check_constraint(
        "messages_projection_identity_complete",
        "messages",
        "(projection_resource_kind IS NULL) = (projection_source_key IS NULL)",
    )
    op.create_check_constraint(
        "messages_projection_resource_kind_check",
        "messages",
        "projection_resource_kind IS NULL OR projection_resource_kind IN ('build', 'vote_session')",
    )
    op.create_check_constraint(
        "messages_desired_action_check",
        "messages",
        "desired_action IN ('refresh', 'delete')",
    )
    op.create_check_constraint(
        "messages_projection_revisions_valid",
        "messages",
        "desired_revision > 0 AND applied_revision > 0 AND applied_revision <= desired_revision",
    )
    op.create_index(
        "messages_projection_pending_idx",
        "messages",
        ["desired_revision"],
        unique=False,
        postgresql_where=sa.text("projection_resource_kind IS NOT NULL AND desired_revision > applied_revision"),
    )
    for entity in _selected_entities(PGFunction, _FUNCTION_NAMES):
        op.execute(entity.to_sql_statement_create())
    for entity in _selected_entities(PGTrigger, _TRIGGER_NAMES):
        op.execute(entity.to_sql_statement_create())


def downgrade() -> None:
    """Remove desired projection state and restore cascading message cleanup."""
    for entity in reversed(_selected_entities(PGTrigger, _TRIGGER_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    for entity in reversed(_selected_entities(PGFunction, _FUNCTION_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    op.drop_index("messages_projection_pending_idx", table_name="messages")
    op.drop_constraint("messages_projection_revisions_valid", "messages", type_="check")
    op.drop_constraint("messages_desired_action_check", "messages", type_="check")
    op.drop_constraint("messages_projection_resource_kind_check", "messages", type_="check")
    op.drop_constraint("messages_projection_identity_complete", "messages", type_="check")
    op.drop_constraint("public_messages_build_id_fkey", "messages", type_="foreignkey")
    op.create_foreign_key(
        "public_messages_build_id_fkey",
        "messages",
        "builds",
        ["build_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("messages", "applied_revision")
    op.drop_column("messages", "desired_revision")
    op.drop_column("messages", "desired_action")
    op.drop_column("messages", "projection_source_key")
    op.drop_column("messages", "projection_resource_kind")
    op.drop_column("discord_sync_queue", "generation")


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]
