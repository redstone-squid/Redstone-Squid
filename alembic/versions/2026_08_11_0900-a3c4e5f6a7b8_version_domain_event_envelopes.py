"""Version domain-event envelopes and centralize publication.

Revision ID: a3c4e5f6a7b8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-11 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction

from alembic import op
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

revision: str = "a3c4e5f6a7b8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_EMIT_SQL = """
CREATE OR REPLACE FUNCTION public.emit_domain_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_type text;
    target_kind text;
    target_id bigint;
    target_payload jsonb;
    new_event_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        IF OLD.submission_status IS NOT DISTINCT FROM NEW.submission_status THEN RETURN NULL; END IF;
        target_kind := 'build';
        target_id := NEW.id;
        IF NEW.submission_status = 1 THEN
            target_type := 'build.confirmed';
        ELSIF NEW.submission_status = 2 THEN
            target_type := 'build.denied';
        ELSE
            RETURN NULL;
        END IF;
        target_payload := jsonb_build_object(
            'previous_status', OLD.submission_status,
            'status', NEW.submission_status
        );
    ELSE
        IF OLD.status IS NOT DISTINCT FROM NEW.status OR NEW.status <> 'closed' THEN RETURN NULL; END IF;
        target_kind := 'vote_session';
        target_id := NEW.id;
        target_type := 'vote_session.closed';
        target_payload := jsonb_build_object('kind', NEW.kind, 'result', NEW.result);
    END IF;

    INSERT INTO public.domain_events (event_type, aggregate_kind, aggregate_id, payload, occurred_at)
    VALUES (target_type, target_kind, target_id, target_payload, now())
    RETURNING id INTO new_event_id;

    INSERT INTO public.domain_event_deliveries
        (event_id, consumer, available_at, claimed_at, attempts, last_error)
    SELECT new_event_id, consumers.name, now(), NULL, 0, NULL
    FROM public.domain_event_consumers AS consumers;
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    """Add envelope versions and route trigger publication through one function."""
    op.add_column(
        "domain_events",
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint("domain_events_schema_version_positive", "domain_events", "schema_version > 0")
    for function_name in ("publish_domain_event", "emit_domain_event"):
        for statement in _function(function_name).to_sql_statement_create_or_replace():
            op.execute(statement)


def downgrade() -> None:
    """Restore direct unversioned trigger publication."""
    op.execute(_LEGACY_EMIT_SQL)
    op.execute("DROP FUNCTION public.publish_domain_event(text, integer, text, bigint, jsonb)")
    op.drop_constraint("domain_events_schema_version_positive", "domain_events", type_="check")
    op.drop_column("domain_events", "schema_version")


def _function(name: str) -> PGFunction:
    return next(
        entity
        for entity in ALEMBIC_UTIL_ENTITIES
        if isinstance(entity, PGFunction) and entity.signature.partition("(")[0] == name
    )
