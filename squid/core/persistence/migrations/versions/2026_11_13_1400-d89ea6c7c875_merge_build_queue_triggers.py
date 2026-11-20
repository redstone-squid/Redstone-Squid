"""Queue all work for a build change from one trigger per table.

Eight build tables each carried a search trigger and a Discord sync trigger that fired on the same
events, so adding a table meant remembering both. `enqueue_build_change` now queues the search
refresh, the record recompute, and the Discord sync together. `build_links` uses it too and still
queues only the Discord sync. Votes and vote sessions keep their own function, renamed
`enqueue_vote_session_sync` now that it handles nothing else.
"""

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from alembic import op

revision: str = "d89ea6c7c875"
down_revision: str | None = "c78d95b6b764"
branch_labels: str | None = None
depends_on: str | None = None

_FUNCTIONS = [
    "CREATE FUNCTION public.enqueue_build_change() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_build_id bigint;\n    target_kind text;\n    build_exists boolean := true;\n    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';\nBEGIN\n    IF TG_TABLE_NAME = 'builds' THEN\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);\n    ELSE\n        -- When the row is going away with its build, the build's own trigger queues the deletion.\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;\n        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;\n        build_exists := FOUND;\n    END IF;\n\n    -- A link change queues only the Discord sync.\n    IF TG_TABLE_NAME <> 'build_links' THEN\n        PERFORM public.upsert_search_projection_request(\n            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END\n        );\n\n        IF target_kind IN ('door', 'extender') THEN\n            -- As in the upsert_*_request functions, clearing the claim token keeps an older worker\n            -- from acknowledging the new request.\n            INSERT INTO public.record_recompute_queue\n                (scope_key, build_kind, build_id, reasons, enqueued_at, available_at)\n            VALUES (\n                target_kind,\n                target_kind,\n                CASE WHEN build_deleted THEN NULL ELSE target_build_id END,\n                '[\"source_change\"]'::jsonb,\n                now(),\n                now()\n            )\n            ON CONFLICT (scope_key) DO UPDATE\n            SET build_id = EXCLUDED.build_id,\n                reasons = (\n                    SELECT jsonb_agg(DISTINCT reason)\n                    FROM jsonb_array_elements_text(\n                        record_recompute_queue.reasons || EXCLUDED.reasons\n                    ) AS reason\n                ),\n                enqueued_at = EXCLUDED.enqueued_at,\n                available_at = EXCLUDED.available_at,\n                attempts = 0,\n                claimed_at = NULL,\n                claim_token = NULL,\n                last_error = NULL;\n        END IF;\n    END IF;\n\n    IF build_deleted THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');\n    ELSIF build_exists THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');\n\n        -- A review card embeds the build it is voting on, so a build change makes the\n        -- session's cards stale too. Their posts are keyed by session, not by build, so\n        -- the sessions have to be enqueued in their own right.\n        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')\n        FROM public.build_vote_sessions bvs\n        WHERE bvs.build_id = target_build_id;\n    END IF;\n    RETURN NULL;\nEND;\n$$;",
    "CREATE FUNCTION public.enqueue_vote_session_sync() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_key bigint;\nBEGIN\n    IF TG_TABLE_NAME = 'vote_sessions' THEN\n        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        PERFORM public.upsert_discord_sync_request(\n            'vote_session', target_key::text, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'refresh' END\n        );\n    ELSE\n        -- When the vote is going away with its session, the session's own trigger queues the deletion.\n        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END;\n        IF EXISTS (SELECT 1 FROM public.vote_sessions WHERE id = target_key) THEN\n            PERFORM public.upsert_discord_sync_request('vote_session', target_key::text, 'refresh');\n        END IF;\n    END IF;\n    RETURN NULL;\nEND;\n$$;",
]
_TRIGGERS = [
    "CREATE TRIGGER build_creators_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.build_creators\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER build_links_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.build_links\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER build_tag_assignments_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER build_versions_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.build_versions\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER builds_enqueue_build_change\n    AFTER INSERT OR DELETE ON public.builds\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER builds_enqueue_build_change_on_update\n    AFTER UPDATE ON public.builds\n    FOR EACH ROW\n    WHEN (\n        (to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])\n        IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])\n    )\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER door_timing_variants_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER doors_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.doors\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER extender_timing_variants_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER extenders_enqueue_build_change\n    AFTER INSERT OR DELETE OR UPDATE ON public.extenders\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_build_change();",
    "CREATE TRIGGER vote_sessions_enqueue_discord_sync\n    AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_vote_session_sync();",
    "CREATE TRIGGER votes_enqueue_discord_sync\n    AFTER INSERT OR DELETE OR UPDATE ON public.votes\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_vote_session_sync();",
]
_OLD_FUNCTIONS = [
    "CREATE FUNCTION public.enqueue_build_search_projection() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_build_id bigint;\n    target_action text := 'upsert';\n    target_kind text;\nBEGIN\n    IF TG_TABLE_NAME = 'builds' THEN\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);\n        IF TG_OP = 'DELETE' THEN\n            target_action := 'delete';\n        END IF;\n    ELSE\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;\n        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;\n    END IF;\n\n    PERFORM public.upsert_search_projection_request('build', target_build_id::text, target_action);\n\n    IF target_kind IN ('door', 'extender') THEN\n        -- As in the upsert_*_request functions, clearing the claim token keeps an older worker\n        -- from acknowledging the new request.\n        INSERT INTO public.record_recompute_queue\n            (scope_key, build_kind, build_id, reasons, enqueued_at, available_at)\n        VALUES (\n            target_kind,\n            target_kind,\n            CASE WHEN TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE' THEN NULL ELSE target_build_id END,\n            '[\"source_change\"]'::jsonb,\n            now(),\n            now()\n        )\n        ON CONFLICT (scope_key) DO UPDATE\n        SET build_id = EXCLUDED.build_id,\n            reasons = (\n                SELECT jsonb_agg(DISTINCT reason)\n                FROM jsonb_array_elements_text(\n                    record_recompute_queue.reasons || EXCLUDED.reasons\n                ) AS reason\n            ),\n            enqueued_at = EXCLUDED.enqueued_at,\n            available_at = EXCLUDED.available_at,\n            attempts = 0,\n            claimed_at = NULL,\n            claim_token = NULL,\n            last_error = NULL;\n    END IF;\n    RETURN NULL;\nEND;\n$$;",
    "CREATE FUNCTION public.enqueue_discord_sync() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_kind text;\n    target_key bigint;\n    target_action text := 'refresh';\nBEGIN\n    IF TG_TABLE_NAME = 'vote_sessions' THEN\n        target_kind := 'vote_session';\n        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;\n    ELSIF TG_TABLE_NAME = 'votes' THEN\n        target_kind := 'vote_session';\n        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END;\n        IF NOT EXISTS (SELECT 1 FROM public.vote_sessions WHERE id = target_key) THEN RETURN NULL; END IF;\n    ELSIF TG_TABLE_NAME = 'builds' THEN\n        target_kind := 'build';\n        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;\n    ELSE\n        target_kind := 'build';\n        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;\n        IF NOT EXISTS (SELECT 1 FROM public.builds WHERE id = target_key) THEN RETURN NULL; END IF;\n    END IF;\n\n    PERFORM public.upsert_discord_sync_request(target_kind, target_key::text, target_action);\n\n    -- A review card embeds the build it is voting on, so a build change makes the\n    -- session's cards stale too. Their posts are keyed by session, not by build, so\n    -- the sessions have to be enqueued in their own right.\n    IF target_kind = 'build' AND target_action = 'refresh' THEN\n        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')\n        FROM public.build_vote_sessions bvs\n        WHERE bvs.build_id = target_key;\n    END IF;\n    RETURN NULL;\nEND;\n$$;",
]
_OLD_TRIGGERS = [
    "CREATE TRIGGER build_creators_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER build_creators_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER build_links_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER build_tag_assignments_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER build_tag_assignments_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER build_versions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER build_versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER builds_enqueue_discord_sync AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER builds_enqueue_discord_sync_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN ((to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at']) IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])) EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER builds_enqueue_search_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN ((to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at']) IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])) EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER door_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER door_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER doors_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER doors_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER extender_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER extender_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER extenders_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER extenders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER vote_sessions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER votes_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.votes FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
]


def upgrade() -> None:
    for sql in _OLD_TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(sql))
    for sql in _OLD_FUNCTIONS:
        op.drop_entity(PGFunction.from_sql(sql))
    for sql in _FUNCTIONS:
        op.create_entity(PGFunction.from_sql(sql))
    for sql in _TRIGGERS:
        op.create_entity(PGTrigger.from_sql(sql))


def downgrade() -> None:
    for sql in _TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(sql))
    for sql in _FUNCTIONS:
        op.drop_entity(PGFunction.from_sql(sql))
    for sql in _OLD_FUNCTIONS:
        op.create_entity(PGFunction.from_sql(sql))
    for sql in _OLD_TRIGGERS:
        op.create_entity(PGTrigger.from_sql(sql))
