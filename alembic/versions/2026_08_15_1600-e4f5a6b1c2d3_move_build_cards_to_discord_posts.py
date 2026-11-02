"""Move published build cards onto discord_posts

The bot's own build cards were tracked as `messages` rows carrying
`purpose = 'view_confirmed_build'` plus projection columns. Ownership and render
state now live in `discord_posts`; the `messages` row stays, because a message the
bot sent is still a message fact.

`enqueue_discord_sync` also starts enqueueing a changed build's vote sessions. A
review card embeds the build it is voting on, so a build edit makes those cards
stale, and they are keyed by session rather than by build. The old code reached
them by refreshing every message sharing a `build_id`, which no longer describes
how posts are addressed.

Revision ID: e4f5a6b1c2d3
Revises: d3e4f5a6b1c2
Create Date: 2026-08-15 16:00:00.000000+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

from alembic_utils.pg_function import PGFunction
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

revision: str = "e4f5a6b1c2d3"
down_revision: str | Sequence[str] | None = "d3e4f5a6b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REPLACED_FUNCTIONS = {"enqueue_discord_sync"}


def upgrade() -> None:
    """Apply this revision."""
    # Adopt the cards already published, so the reconciler edits them instead of
    # posting duplicates beside them.
    op.execute(
        """
        INSERT INTO discord_posts (
            message_id, channel_id, resource_kind, resource_key, surface, applied_revision, posted_at, rendered_at
        )
        SELECT
            m.id,
            m.channel_id,
            'build',
            m.projection_source_key,
            'build_card',
            m.applied_revision,
            COALESCE(m.observed_at, now()),
            m.updated_at
        FROM messages m
        WHERE m.purpose = 'view_confirmed_build'
          AND m.channel_id IS NOT NULL
          AND m.projection_resource_kind = 'build'
          AND m.projection_source_key IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    # These rows are no longer render targets, so drop the projection identity that
    # made the old reconciler pick them up.
    op.execute(
        """
        UPDATE messages
        SET purpose = NULL, projection_resource_kind = NULL, projection_source_key = NULL
        WHERE purpose = 'view_confirmed_build'
        """
    )
    for entity in _selected_entities(PGFunction, _REPLACED_FUNCTIONS):
        for statement in entity.to_sql_statement_create_or_replace():
            op.execute(statement)


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.execute(_ENQUEUE_DISCORD_SYNC_WITHOUT_VOTE_FANOUT)
    op.execute(
        """
        UPDATE messages m
        SET purpose = 'view_confirmed_build',
            projection_resource_kind = 'build',
            projection_source_key = p.resource_key,
            applied_revision = p.applied_revision,
            desired_revision = GREATEST(m.desired_revision, p.applied_revision)
        FROM discord_posts p
        WHERE p.message_id = m.id AND p.resource_kind = 'build' AND p.surface = 'build_card'
        """
    )
    op.execute("DELETE FROM discord_posts WHERE resource_kind = 'build' AND surface = 'build_card'")


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in ALEMBIC_UTIL_ENTITIES
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]


_ENQUEUE_DISCORD_SYNC_WITHOUT_VOTE_FANOUT = """
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
        (resource_kind, source_key, action, generation, enqueued_at, claimed_at, dead_at, attempts, last_error)
    VALUES (
        target_kind, target_key::text, target_action,
        nextval('public.discord_sync_generation_seq'), now(), NULL, NULL, 0, NULL
    )
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        generation = EXCLUDED.generation,
        enqueued_at = EXCLUDED.enqueued_at,
        claimed_at = NULL,
        dead_at = NULL,
        attempts = 0,
        last_error = NULL;
    RETURN NULL;
END;
$$
"""
