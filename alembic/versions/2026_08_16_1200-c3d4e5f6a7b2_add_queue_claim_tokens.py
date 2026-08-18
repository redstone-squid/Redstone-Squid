"""Give the durable work queues a claim token and a retry clock

Seven tables in this schema are work queues, and the four on `ClaimedRowQueue`
fence a claim with a worker-minted timestamp. A timestamp is a weak token: it
comes from the worker's clock while the reclaim predicate compares against the
database's, and one batch claim stamps every row in it with the same value. This
revision lays down the columns needed to fence on a database-minted UUID instead,
the way `domain_event_deliveries` already does. Nothing reads them yet.

`available_at` is the second half. Five of these tables overloaded `enqueued_at`
as the retry clock, so backing a row off rewrote when the work was requested:
FIFO fairness was lost, because a repeatedly failing row kept jumping to the back
of its own queue, and `squid.queue.oldest_ready_age` under-reported, because a job
that had been failing for an hour looked fresh. One retry clock per table, never
written by the enqueue path.

`claim_token` ships nullable on purpose. `deploy/compose.production.yml` migrates
before replacing the long-running containers, so old code runs against this schema
for one window. It writes `claimed_at` and never a token, which a
`(claimed_at IS NULL) = (claim_token IS NULL)` CHECK would reject mid-drain. The
follow-up revision `enforce_queue_claim_tokens` adds those constraints once every
deployment is past this one. An operator who prefers a single release can instead
`docker compose stop worker bot`, migrate, and `up -d`; do not depend on it.

The window is otherwise safe both ways. Old code fences on `claimed_at`, which
still exists and is still written, so a worker mid-claim across a restart finishes
correctly; and once a new worker reclaims a row past the visibility timeout, the
old worker's `claimed_at`-fenced acknowledgement matches nothing and returns False,
which is the pre-existing correct behaviour. The one artifact is that old code's
`enqueued_at`-based backoff leaves `available_at` in the past, so a new worker
retries such a row immediately -- a latency effect confined to one deploy window.

Revision ID: c3d4e5f6a7b2
Revises: c9d2e3f4a5b6
Create Date: 2026-08-16 12:00:00.000000+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.replaceable_entity import ReplaceableEntity
from sqlalchemy.dialects import postgresql

import squid.persistence.types
from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "c3d4e5f6a7b2"
down_revision: str | Sequence[str] | None = "c9d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REPLACED_FUNCTIONS = {
    "enqueue_build_search_projection",
    "enqueue_metadata_search_projection",
    "enqueue_computed_record_search_projection",
    "enqueue_discord_sync",
    "enqueue_starboard_sync",
}

_RETRY_CLOCKS: tuple[tuple[str, str], ...] = (
    ("discord_sync_queue", "claimed_at"),
    ("schematic_render_queue", "claimed_at"),
    ("search_projection_queue", "locked_at"),
    ("search_embedding_queue", "locked_at"),
    ("record_recompute_queue", "locked_at"),
)
"""The five tables gaining `available_at`, with the column each one claims through."""

_DISCORD_SYNC_QUEUE_COMMENT = (
    "A coalesced request to refresh one Discord-rendered resource.\n"
    "\n"
    "Not an event queue, despite the shape. Rows are coalesced on\n"
    "`(resource_kind, source_key)` by the six `INSERT ... ON CONFLICT ... DO\n"
    "UPDATE` triggers in `squid/persistence/postgres_entities.sql` — that is where\n"
    "the coalescing actually happens — deleted on acknowledgement, and carry a\n"
    "`generation` compared against a post's applied revision as a staleness token\n"
    "rather than read as an ordering. An event log would be append-only and\n"
    "replayable; this table is neither. A row means \"this resource's Discord posts\n"
    'are stale", and re-reading it tells you what the resource should look like\n'
    "now, not what happened to it.\n"
    "\n"
    "Discord-specific on purpose: every row exists to repair a Discord post, and\n"
    "the only consumer renders them (`squid/bot/sync/reconciler.py`). The\n"
    "application layer speaks of *reconciliation* rather than *sync* because a\n"
    "sync names a transfer, and nothing here transfers anything."
)

_TABLE_COMMENT_DRIFT: dict[str, tuple[str, str]] = {
    "accounts": (
        "An internal caller independent of every external identity provider.",
        "An internal principal independent of every external identity provider.",
    ),
    "discord_sync_queue": (
        _DISCORD_SYNC_QUEUE_COMMENT,
        "A coalesced request to refresh one Discord-rendered resource.",
    ),
    "vote_sessions": (
        "A voting session for builds, log deletions, or generic polls.",
        "A voting session for builds or log deletions.",
    ),
}
"""Table comments the models moved past, as (current model text, deployed text)."""

_READY_INDEX_PREDICATES: dict[str, str] = {
    "discord_sync_queue": "claimed_at IS NULL AND dead_at IS NULL",
    "schematic_render_queue": "claimed_at IS NULL AND dead_at IS NULL",
    "search_projection_queue": "locked_at IS NULL AND dead_at IS NULL",
    "search_embedding_queue": "locked_at IS NULL AND dead_at IS NULL",
    "record_recompute_queue": "locked_at IS NULL",
}


def upgrade() -> None:
    """Apply this revision."""
    for table, claim_column in _RETRY_CLOCKS:
        # Adding a NOT NULL column with a non-volatile default is metadata-only on
        # PostgreSQL 11+, so none of these rewrites the table.
        op.add_column(
            table,
            sa.Column(
                "available_at",
                squid.persistence.types.InstantUTC(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        # Rows already in flight keep the readiness they had, including any backoff
        # the old code wrote into `enqueued_at`.
        op.execute(f"UPDATE public.{table} SET available_at = enqueued_at")
        op.add_column(
            table,
            sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        )
        # A row claimed by the running release has no token; mint one so the new
        # code's fence has something to compare, and so revision 2 can add its CHECK.
        op.execute(f"UPDATE public.{table} SET claim_token = gen_random_uuid() WHERE {claim_column} IS NOT NULL")
        op.drop_index(f"{table}_ready_idx", table_name=table)
        op.create_index(
            f"{table}_ready_idx",
            table,
            ["available_at"],
            unique=False,
            postgresql_where=sa.text(_READY_INDEX_PREDICATES[table]),
        )

    op.add_column(
        "schematic_jobs",
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute("UPDATE public.schematic_jobs SET claim_token = gen_random_uuid() WHERE claimed_at IS NOT NULL")

    for entity in _selected_entities(PGFunction, _REPLACED_FUNCTIONS):
        # `to_sql_statement_create_or_replace` yields several statements: alembic_utils
        # renames the live function aside, creates the new one, then drops the old.
        for statement in entity.to_sql_statement_create_or_replace():
            op.execute(statement)

    # Table-comment drift this branch accumulated while it had two heads: with
    # `alembic upgrade head` ambiguous, `alembic check` could not run at all, so three
    # docstring edits never reached the database. They are carried over here.
    for table, (new_comment, old_comment) in _TABLE_COMMENT_DRIFT.items():
        op.create_table_comment(table, new_comment, existing_comment=old_comment)


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    for table, (new_comment, old_comment) in _TABLE_COMMENT_DRIFT.items():
        op.create_table_comment(table, old_comment, existing_comment=new_comment)

    for body in (
        _ENQUEUE_BUILD_SEARCH_PROJECTION_BEFORE_CLAIM_TOKENS,
        _ENQUEUE_METADATA_SEARCH_PROJECTION_BEFORE_CLAIM_TOKENS,
        _ENQUEUE_COMPUTED_RECORD_SEARCH_PROJECTION_BEFORE_CLAIM_TOKENS,
        _ENQUEUE_DISCORD_SYNC_BEFORE_CLAIM_TOKENS,
        _ENQUEUE_STARBOARD_SYNC_BEFORE_CLAIM_TOKENS,
    ):
        op.execute(body)

    op.drop_column("schematic_jobs", "claim_token")
    for table, _ in _RETRY_CLOCKS:
        op.drop_index(f"{table}_ready_idx", table_name=table)
        op.create_index(
            f"{table}_ready_idx",
            table,
            ["enqueued_at"],
            unique=False,
            postgresql_where=sa.text(_READY_INDEX_PREDICATES[table]),
        )
        op.drop_column(table, "claim_token")
        op.drop_column(table, "available_at")


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(entity_type: type[EntityT], names: set[str]) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]


_ENQUEUE_BUILD_SEARCH_PROJECTION_BEFORE_CLAIM_TOKENS = """
CREATE OR REPLACE FUNCTION public.enqueue_build_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_build_id bigint;
    target_action text := 'upsert';
    target_kind text;
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);
        IF TG_OP = 'DELETE' THEN
            target_action := 'delete';
        END IF;
    ELSE
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('build', target_build_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        dead_at = NULL,
        last_error = NULL;

    IF target_kind IN ('door', 'extender') THEN
        INSERT INTO public.record_recompute_queue
            (scope_key, build_kind, build_id, reasons, enqueued_at)
        VALUES (
            target_kind,
            target_kind,
            CASE WHEN TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE' THEN NULL ELSE target_build_id END,
            '["source_change"]'::jsonb,
            now()
        )
        ON CONFLICT (scope_key) DO UPDATE
        SET build_id = EXCLUDED.build_id,
            reasons = (
                SELECT jsonb_agg(DISTINCT reason)
                FROM jsonb_array_elements_text(
                    record_recompute_queue.reasons || EXCLUDED.reasons
                ) AS reason
            ),
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$
"""

_ENQUEUE_METADATA_SEARCH_PROJECTION_BEFORE_CLAIM_TOKENS = """
CREATE OR REPLACE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'tag_definitions' THEN 'tag'
        WHEN 'tag_aliases' THEN 'tag'
        WHEN 'creator_aliases' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'tag_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.tag_id ELSE NEW.tag_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'tag_aliases' THEN
        target_action := 'delete';
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('metadata', target_kind || ':' || target_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        dead_at = NULL,
        last_error = NULL;

    IF TG_TABLE_NAME IN ('tag_definitions', 'tag_aliases') THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', assignment.build_id::text, 'upsert', now()
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert',
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            dead_at = NULL,
            last_error = NULL;

        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, claimed_at, dead_at, attempts, last_error)
        SELECT
            'build', assignment.build_id::text, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), NULL, NULL, 0, NULL
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action,
            generation = EXCLUDED.generation,
            enqueued_at = EXCLUDED.enqueued_at,
            claimed_at = NULL,
            dead_at = NULL,
            attempts = 0,
            last_error = NULL;
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bc.build_id::text, 'upsert', now()
        FROM public.build_creators bc
        WHERE bc.alias_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bv.build_id::text, 'upsert', now()
        FROM public.build_versions bv
        WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$
"""

_ENQUEUE_COMPUTED_RECORD_SEARCH_PROJECTION_BEFORE_CLAIM_TOKENS = """
CREATE OR REPLACE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_result_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'record_results' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        VALUES (
            'record',
            'result:' || target_result_id::text,
            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
            now()
        )
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'record_result_holders' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END;
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        VALUES ('record', 'result:' || target_result_id::text, 'upsert', now())
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, dead_at = NULL, last_error = NULL;
    ELSE
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'record', 'result:' || rr.id::text, 'upsert', now()
        FROM public.record_results rr
        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0, locked_at = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$
"""

_ENQUEUE_DISCORD_SYNC_BEFORE_CLAIM_TOKENS = """
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

    -- The generation is drawn from a sequence rather than counted per row. Acknowledging
    -- a job deletes its queue row, so a per-row counter restarted at 1 on the next edit
    -- and could name a revision below one already applied. Sequences are exempt from
    -- rollback, which is exactly what a staleness token needs.
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

    -- A review card embeds the build it is voting on, so a build change makes the
    -- session's cards stale too. Their posts are keyed by session, not by build, so
    -- the sessions have to be enqueued in their own right.
    IF target_kind = 'build' AND target_action = 'refresh' THEN
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, claimed_at, dead_at, attempts, last_error)
        SELECT
            'vote_session', bvs.vote_session_id::text, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), NULL, NULL, 0, NULL
        FROM public.build_vote_sessions bvs
        WHERE bvs.build_id = target_key
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation,
            enqueued_at = EXCLUDED.enqueued_at,
            claimed_at = NULL,
            dead_at = NULL,
            attempts = 0,
            last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$
"""

_ENQUEUE_STARBOARD_SYNC_BEFORE_CLAIM_TOKENS = """
CREATE OR REPLACE FUNCTION public.enqueue_starboard_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_key text;
BEGIN
    IF TG_TABLE_NAME = 'starboards' THEN
        -- A configuration change restyles or re-thresholds every entry on the board.
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, claimed_at, dead_at, attempts, last_error)
        SELECT
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), NULL, NULL, 0, NULL
        FROM public.starboard_entries e
        WHERE e.starboard_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
            claimed_at = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
        RETURN NULL;
    END IF;

    IF TG_TABLE_NAME = 'starboard_origin_messages' THEN
        target_key := NULL;
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, claimed_at, dead_at, attempts, last_error)
        SELECT
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), NULL, NULL, 0, NULL
        FROM public.starboard_entries e
        WHERE e.origin_message_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
            claimed_at = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
        RETURN NULL;
    END IF;

    IF TG_OP = 'DELETE' THEN
        target_key := OLD.starboard_id || ':' || OLD.origin_message_id;
    ELSE
        target_key := NEW.starboard_id || ':' || NEW.origin_message_id;
    END IF;
    INSERT INTO public.discord_sync_queue
        (resource_kind, source_key, action, generation, enqueued_at, claimed_at, dead_at, attempts, last_error)
    VALUES (
        'starboard_entry', target_key, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'refresh' END,
        nextval('public.discord_sync_generation_seq'), now(), NULL, NULL, 0, NULL
    )
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action, generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
        claimed_at = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
    RETURN NULL;
END;
$$
"""
