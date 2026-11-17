"""Capture concrete inputs and prevent publication from regressing their generation."""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4f8b2c6e051"
down_revision: str | None = "c3e7a1b5d940"
branch_labels: str | None = None
depends_on: str | None = None

_OLD_ENQUEUE = """
CREATE FUNCTION public.enqueue_registered_door_ranges() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.build_kind = 'door' THEN
        INSERT INTO public.door_range_recompute_queue (query_id)
        SELECT id FROM public.door_range_registrations ORDER BY id
        ON CONFLICT (query_id) DO UPDATE SET
            enqueued_at = now(), available_at = now(),
            claimed_at = NULL, claim_token = NULL;
    END IF;
    RETURN NULL;
END;
$$;
"""
_ENQUEUE = """
CREATE FUNCTION public.enqueue_registered_door_ranges() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE public.record_input_generations SET generation = generation + 1
    WHERE build_kind = NEW.build_kind;
    IF NEW.build_kind = 'door' THEN
        INSERT INTO public.door_range_recompute_queue (query_id)
        SELECT id FROM public.door_range_registrations ORDER BY id
        ON CONFLICT (query_id) DO UPDATE SET
            enqueued_at = now(), available_at = now(),
            claimed_at = NULL, claim_token = NULL;
    END IF;
    RETURN NULL;
END;
$$;
"""
_IMMUTABLE = """
CREATE FUNCTION public.reject_record_input_snapshot_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Record input snapshots are immutable' USING ERRCODE = '23514';
END;
$$;
"""
_TRIGGER = """
CREATE TRIGGER record_input_snapshots_immutable
BEFORE UPDATE OR DELETE ON public.record_input_snapshots
FOR EACH ROW EXECUTE FUNCTION public.reject_record_input_snapshot_mutation();
"""

_OLD_BUILD = """
CREATE FUNCTION public.enqueue_build_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_build_id bigint;
    target_kind text;
    build_exists boolean := true;
    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);
    ELSE
        -- When the row is going away with its build, the build's own trigger queues the deletion.
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;
        build_exists := FOUND;
    END IF;

    -- A link change queues only the Discord sync.
    IF TG_TABLE_NAME <> 'build_links' THEN
        PERFORM public.upsert_search_projection_request(
            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END
        );

        IF target_kind IN ('door', 'extender') THEN
            -- As in the upsert_*_request functions, clearing the claim token keeps an older worker
            -- from acknowledging the new request.
            INSERT INTO public.record_recompute_queue
                (scope_key, build_kind, build_id, reasons, enqueued_at, available_at)
            VALUES (
                target_kind,
                target_kind,
                CASE WHEN build_deleted THEN NULL ELSE target_build_id END,
                '["source_change"]'::jsonb,
                now(),
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
                available_at = EXCLUDED.available_at,
                attempts = 0,
                claimed_at = NULL,
                claim_token = NULL,
                last_error = NULL;
        END IF;
    END IF;

    IF build_deleted THEN
        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');
    ELSIF build_exists THEN
        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');

        -- A review card embeds the build it is voting on, so a build change makes the
        -- session's cards stale too. Their posts are keyed by session, not by build, so
        -- the sessions have to be enqueued in their own right.
        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')
        FROM public.build_vote_sessions bvs
        WHERE bvs.build_id = target_build_id;
    END IF;
    RETURN NULL;
END;
$$;
"""
_BUILD = """
CREATE FUNCTION public.enqueue_build_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_build_id bigint;
    target_kind text;
    previous_kind text;
    build_exists boolean := true;
    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);
        IF TG_OP = 'UPDATE' THEN
            previous_kind := lower(OLD.category);
        END IF;
    ELSE
        -- When the row is going away with its build, the build's own trigger queues the deletion.
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;
        build_exists := FOUND;
    END IF;

    -- A link change queues only the Discord sync.
    IF TG_TABLE_NAME <> 'build_links' THEN
        PERFORM public.upsert_search_projection_request(
            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END
        );

        IF target_kind IN ('door', 'extender') OR previous_kind IN ('door', 'extender') THEN
            -- As in the upsert_*_request functions, clearing the claim token keeps an older worker
            -- from acknowledging the new request.
            INSERT INTO public.record_recompute_queue
                (scope_key, build_kind, build_id, reasons, enqueued_at, available_at)
            SELECT kind, kind,
                CASE WHEN build_deleted THEN NULL ELSE target_build_id END,
                '["source_change"]'::jsonb, now(), now()
            FROM (
                SELECT DISTINCT kind FROM unnest(ARRAY[target_kind, previous_kind]) AS kinds(kind)
                WHERE kind IN ('door', 'extender')
            ) AS affected
            ORDER BY kind
            ON CONFLICT (scope_key) DO UPDATE
            SET build_id = EXCLUDED.build_id,
                reasons = (
                    SELECT jsonb_agg(DISTINCT reason)
                    FROM jsonb_array_elements_text(
                        record_recompute_queue.reasons || EXCLUDED.reasons
                    ) AS reason
                ),
                enqueued_at = EXCLUDED.enqueued_at,
                available_at = EXCLUDED.available_at,
                attempts = 0,
                claimed_at = NULL,
                claim_token = NULL,
                last_error = NULL;
        END IF;
    END IF;

    IF build_deleted THEN
        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');
    ELSIF build_exists THEN
        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');

        -- A review card embeds the build it is voting on, so a build change makes the
        -- session's cards stale too. Their posts are keyed by session, not by build, so
        -- the sessions have to be enqueued in their own right.
        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')
        FROM public.build_vote_sessions bvs
        WHERE bvs.build_id = target_build_id;
    END IF;
    RETURN NULL;
END;
$$;
"""
_TAXONOMY = """
CREATE FUNCTION public.enqueue_record_taxonomy_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.record_recompute_queue (scope_key, build_kind, reasons)
    VALUES ('door', 'door', '["taxonomy_change"]'::jsonb),
           ('extender', 'extender', '["taxonomy_change"]'::jsonb)
    ON CONFLICT (scope_key) DO UPDATE SET
        enqueued_at = now(), available_at = now(),
        claimed_at = NULL, claim_token = NULL;
    RETURN NULL;
END;
$$;
"""
_TAGS = """
CREATE TRIGGER tag_definitions_enqueue_records
AFTER INSERT OR UPDATE OR DELETE ON public.tag_definitions
FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_taxonomy_change();
"""
_UNITS = """
CREATE TRIGGER tag_units_enqueue_records
AFTER INSERT OR UPDATE OR DELETE ON public.tag_units
FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_taxonomy_change();
"""


def upgrade() -> None:
    op.create_table(
        "record_input_generations",
        sa.Column("build_kind", sa.Text(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("build_kind"),
        sa.CheckConstraint("build_kind IN ('door', 'extender')", name="record_input_generations_kind_check"),
        sa.CheckConstraint("generation >= 0", name="record_input_generations_generation_check"),
        comment="A permanent per-kind counter changed atomically with record invalidation.",
    )
    op.execute("INSERT INTO record_input_generations (build_kind) VALUES ('door'), ('extender')")
    op.create_table(
        "record_input_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(jsonb_typeof(document) = 'object' AND document->'schema_version' = '1'::jsonb) IS TRUE",
            name="record_input_snapshots_version_check",
        ),
        comment="Immutable concrete inputs saved before arithmetic starts.",
    )
    for name in ("input_snapshot_id", "checked_snapshot_id"):
        op.add_column("record_computation_runs", sa.Column(name, sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"record_computation_runs_{name}_fkey",
            "record_computation_runs",
            "record_input_snapshots",
            [name],
            ["id"],
            ondelete="RESTRICT",
        )
    op.add_column("record_computation_runs", sa.Column("checked_generation", sa.BigInteger(), nullable=True))
    op.replace_entity(PGFunction.from_sql(_ENQUEUE.strip()))
    op.create_entity(PGFunction.from_sql(_IMMUTABLE.strip()))
    op.create_entity(PGTrigger.from_sql(_TRIGGER.strip()))
    op.replace_entity(PGFunction.from_sql(_BUILD.strip()))
    op.create_entity(PGFunction.from_sql(_TAXONOMY.strip()))
    op.create_entity(PGTrigger.from_sql(_TAGS.strip()))
    op.create_entity(PGTrigger.from_sql(_UNITS.strip()))
    op.execute("""
        INSERT INTO record_recompute_queue (scope_key, build_kind, reasons)
        VALUES ('door', 'door', '["snapshot_capture"]'::jsonb),
               ('extender', 'extender', '["snapshot_capture"]'::jsonb)
        ON CONFLICT (scope_key) DO UPDATE SET
            enqueued_at = now(), available_at = now(), claimed_at = NULL, claim_token = NULL
    """)


def downgrade() -> None:
    op.drop_entity(PGTrigger.from_sql(_UNITS.strip()))
    op.drop_entity(PGTrigger.from_sql(_TAGS.strip()))
    op.drop_entity(PGFunction.from_sql(_TAXONOMY.strip()))
    op.replace_entity(PGFunction.from_sql(_OLD_BUILD.strip()))
    op.drop_entity(PGTrigger.from_sql(_TRIGGER.strip()))
    op.drop_entity(PGFunction.from_sql(_IMMUTABLE.strip()))
    op.replace_entity(PGFunction.from_sql(_OLD_ENQUEUE.strip()))
    for name in ("input_snapshot_id", "checked_snapshot_id"):
        op.drop_constraint(f"record_computation_runs_{name}_fkey", "record_computation_runs", type_="foreignkey")
        op.drop_column("record_computation_runs", name)
    op.drop_column("record_computation_runs", "checked_generation")
    op.drop_table("record_input_snapshots")
    op.drop_table("record_input_generations")
