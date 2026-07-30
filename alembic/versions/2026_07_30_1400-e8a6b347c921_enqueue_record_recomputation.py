"""enqueue record recomputation

Revision ID: e8a6b347c921
Revises: d42be8a917c3
Create Date: 2026-07-30 14:00:00+00:00
"""

from collections.abc import Sequence

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from alembic import op
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

revision: str = "e8a6b347c921"
down_revision: str | Sequence[str] | None = "d42be8a917c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enqueue record work from the existing build projection trigger."""
    function = _enqueue_build_function()
    for statement in function.to_sql_statement_create_or_replace():
        op.execute(statement)
    for trigger in _timing_triggers():
        op.execute(trigger.to_sql_statement_create())


def downgrade() -> None:
    """Restore the search-only build projection trigger function."""
    for trigger in reversed(_timing_triggers()):
        op.execute(trigger.to_sql_statement_drop())
    op.execute(_PREVIOUS_FUNCTION)


def _enqueue_build_function() -> PGFunction:
    return next(
        entity
        for entity in ALEMBIC_UTIL_ENTITIES
        if isinstance(entity, PGFunction) and entity.signature.partition("(")[0] == "enqueue_build_search_projection"
    )


def _timing_triggers() -> list[PGTrigger]:
    names = {"door_timing_variants_enqueue_search", "extender_timing_variants_enqueue_search"}
    return [entity for entity in ALEMBIC_UTIL_ENTITIES if isinstance(entity, PGTrigger) and entity.signature in names]


_PREVIOUS_FUNCTION = """
CREATE OR REPLACE FUNCTION public.enqueue_build_search_projection() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_build_id bigint;
    target_action text := 'upsert';
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN
            target_action := 'delete';
        END IF;
    ELSE
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('build', target_build_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;
    RETURN NULL;
END;
$$;
"""
