"""Retain exhausted outbox work as dead letters.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-10 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ENQUEUE_DISCORD_SYNC = """
CREATE OR REPLACE FUNCTION public.enqueue_discord_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_kind text;
    target_key bigint;
    target_action text := 'refresh';
BEGIN
    IF TG_TABLE_NAME = 'vote_sessions' THEN
        target_kind := 'vote_session';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;
    ELSIF TG_TABLE_NAME = 'votes' THEN
        target_kind := 'vote_session';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END;
        IF NOT EXISTS (SELECT 1 FROM public.vote_sessions WHERE id = target_key) THEN RETURN NULL; END IF;
    ELSIF TG_TABLE_NAME = 'builds' THEN
        target_kind := 'build';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;
    ELSE
        target_kind := 'build';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        IF NOT EXISTS (SELECT 1 FROM public.builds WHERE id = target_key) THEN RETURN NULL; END IF;
    END IF;

    INSERT INTO public.discord_sync_queue
        (resource_kind, source_key, action, enqueued_at, claimed_at, attempts, last_error)
    VALUES (target_kind, target_key::text, target_action, now(), NULL, 0, NULL)
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        claimed_at = NULL,
        attempts = 0,
        last_error = NULL;
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    """Add dead-letter state and prevent it from being claimed."""
    op.add_column("discord_sync_queue", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("domain_event_deliveries", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_index("discord_sync_queue_ready_idx", table_name="discord_sync_queue")
    op.create_index(
        "discord_sync_queue_ready_idx",
        "discord_sync_queue",
        ["enqueued_at"],
        unique=False,
        postgresql_where=sa.text("claimed_at IS NULL AND dead_at IS NULL"),
    )
    op.drop_index("domain_event_deliveries_ready_idx", table_name="domain_event_deliveries")
    op.create_index(
        "domain_event_deliveries_ready_idx",
        "domain_event_deliveries",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("claimed_at IS NULL AND dead_at IS NULL"),
    )
    op.execute(_current_enqueue_discord_sync_sql())


def downgrade() -> None:
    """Restore deletion-at-ceiling schema support."""
    op.execute(_OLD_ENQUEUE_DISCORD_SYNC)

    op.drop_index("domain_event_deliveries_ready_idx", table_name="domain_event_deliveries")
    op.create_index(
        "domain_event_deliveries_ready_idx",
        "domain_event_deliveries",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("claimed_at IS NULL"),
    )
    op.drop_index("discord_sync_queue_ready_idx", table_name="discord_sync_queue")
    op.create_index(
        "discord_sync_queue_ready_idx",
        "discord_sync_queue",
        ["enqueued_at"],
        unique=False,
        postgresql_where=sa.text("claimed_at IS NULL"),
    )
    op.drop_column("domain_event_deliveries", "dead_at")
    op.drop_column("discord_sync_queue", "dead_at")


def _current_enqueue_discord_sync_sql() -> str:
    for entity in alembic_util_entities():
        if isinstance(entity, PGFunction) and entity.signature.partition("(")[0] == "enqueue_discord_sync":
            statement = str(entity.to_sql_statement_create())
            return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    msg = "enqueue_discord_sync function is missing from the Alembic entity registry"
    raise RuntimeError(msg)
