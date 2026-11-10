"""Capture build history automatically inside an attributed transaction.

Stop old writers before upgrading and deploy scope-aware writers before resuming.
Existing snapshots and history entries are preserved.
"""

from alembic import op

revision = "e1b6a9c4d7f0"
down_revision = "d0a5f8b3c6e9"
branch_labels = None
depends_on = None

_UPGRADE = r"""

CREATE TABLE build_edit_pending (
	transaction_id BIGINT NOT NULL,
	build_id BIGINT NOT NULL,
	context JSONB NOT NULL,
	before_snapshot JSONB,
	before_revision BIGINT,
	CONSTRAINT build_edit_pending_pkey PRIMARY KEY (transaction_id, build_id),
	CONSTRAINT build_edit_pending_before_check CHECK ((before_snapshot IS NULL) = (before_revision IS NULL))
)

;
COMMENT ON TABLE build_edit_pending IS 'Transaction-local work removed by the deferred history trigger before commit.';
CREATE FUNCTION public.begin_build_edit(edit_context jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF nullif(current_setting('squid.build_edit_context', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'Only one build editing scope is allowed per transaction' USING ERRCODE = '55000';
    END IF;
    IF jsonb_typeof(edit_context) IS DISTINCT FROM 'object'
        OR nullif(btrim(edit_context->>'source'), '') IS NULL
        OR num_nonnulls(edit_context->>'actor_account_id', edit_context->>'actor_system') <> 1
        OR (edit_context->>'actor_account_id')::integer <= 0
        OR (edit_context->>'actor_system' IS NOT NULL AND btrim(edit_context->>'actor_system') = '')
        OR (edit_context->>'correlation_kind' IS NULL) <> (edit_context->>'correlation_id' IS NULL)
        OR (edit_context->>'operation') IS NULL
        OR (edit_context->>'operation') NOT IN ('create', 'edit', 'moderation', 'recalculation', 'schematic', 'account_merge', 'delete')
        OR (edit_context->>'operation_id') IS NULL
    THEN
        RAISE EXCEPTION 'Invalid build edit context' USING ERRCODE = '22023';
    END IF;
    PERFORM (edit_context->>'operation_id')::uuid;
    PERFORM set_config('squid.build_edit_context',
        (edit_context || jsonb_build_object('transaction_id', txid_current()))::text, true);
    PERFORM set_config('squid.build_edit_open', 'true', true);
END;
$$;

CREATE FUNCTION public.close_build_edit() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM set_config('squid.build_edit_open', 'false', true);
END;
$$;

CREATE FUNCTION public.capture_build_edit(target_build_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    context_value jsonb;
    previous_snapshot jsonb;
    previous_revision bigint;
BEGIN
    context_value := nullif(current_setting('squid.build_edit_context', true), '')::jsonb;
    IF context_value IS NULL OR (context_value->>'transaction_id')::bigint <> txid_current()
        OR current_setting('squid.build_edit_open', true) IS DISTINCT FROM 'true'
    THEN
        RAISE EXCEPTION 'Build changes require an active build editing scope' USING ERRCODE = '55000';
    END IF;
    IF current_setting('squid.build_edit_finalized', true) = txid_current()::text THEN
        RAISE EXCEPTION 'Build history was finalized; no further build changes are allowed' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (SELECT 1 FROM public.build_edit_pending
               WHERE transaction_id = txid_current() AND build_id = target_build_id) THEN
        RETURN;
    END IF;
    -- Serialize every aggregate before taking its first pre-edit snapshot.
    SELECT revision INTO previous_revision FROM public.builds WHERE id = target_build_id FOR UPDATE;
    previous_snapshot := public.build_history_snapshot_v1(target_build_id);
    INSERT INTO public.build_edit_pending(transaction_id, build_id, context, before_snapshot, before_revision)
    VALUES (txid_current(), target_build_id, context_value, previous_snapshot, previous_revision);
END;
$$;

CREATE FUNCTION public.capture_build_edit_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    old_value jsonb;
    new_value jsonb;
    ignored text[] := ARRAY[]::text[];
    build_ids bigint[];
    target bigint;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'Use DELETE inside a build editing scope instead of TRUNCATE' USING ERRCODE = '55000';
    END IF;
    old_value := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END;
    new_value := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END;
    IF TG_TABLE_NAME = 'builds' THEN
        ignored := ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at', 'revision', 'edited_time'];
    ELSIF TG_TABLE_NAME = 'build_tag_assignments' THEN
        ignored := ARRAY['created_at', 'updated_at'];
    ELSIF TG_TABLE_NAME = 'build_schematics' THEN
        -- The history contract retains attachment/publication metadata, not recomputable analyses.
        SELECT jsonb_object_agg(key, value) INTO old_value FROM jsonb_each(old_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
        SELECT jsonb_object_agg(key, value) INTO new_value FROM jsonb_each(new_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        ignored := ARRAY['normalized_name','claimed_at','claim_method','created_at'];
    END IF;
    IF (old_value - ignored) IS NOT DISTINCT FROM (new_value - ignored) THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_TABLE_NAME = 'creator_aliases' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_creators
        WHERE alias_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'versions' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_versions
        WHERE version_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'builds' THEN
        build_ids := ARRAY[(old_value->>'id')::bigint, (new_value->>'id')::bigint];
    ELSE
        build_ids := ARRAY[(old_value->>'build_id')::bigint, (new_value->>'build_id')::bigint];
    END IF;
    FOR target IN SELECT DISTINCT value FROM unnest(build_ids) AS value WHERE value IS NOT NULL ORDER BY value LOOP
        PERFORM public.capture_build_edit(target);
    END LOOP;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE FUNCTION public.finish_build_edit_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    pending public.build_edit_pending%ROWTYPE;
    final_snapshot jsonb;
    final_revision bigint;
    before_id bigint;
    after_id bigint;
    action text;
BEGIN
    SELECT * INTO pending FROM public.build_edit_pending
    WHERE transaction_id = NEW.transaction_id AND build_id = NEW.build_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    PERFORM set_config('squid.build_edit_finalized', txid_current()::text, true);
    final_snapshot := public.build_history_snapshot_v1(pending.build_id);
    SELECT revision INTO final_revision FROM public.builds WHERE id = pending.build_id;
    IF pending.before_snapshot IS DISTINCT FROM final_snapshot THEN
        IF pending.before_snapshot IS NOT NULL THEN
            SELECT s.id INTO before_id FROM public.build_edit_history h
            JOIN public.build_history_states s ON s.id = h.after_state_id
            WHERE h.id = (SELECT max(id) FROM public.build_edit_history WHERE build_id = pending.build_id)
                AND s.snapshot = pending.before_snapshot AND s.build_revision = pending.before_revision
                AND s.schema_version = 1;
            IF before_id IS NULL THEN
                INSERT INTO public.build_history_states(build_id, build_revision, schema_version, snapshot)
                VALUES (pending.build_id, pending.before_revision, 1, pending.before_snapshot) RETURNING id INTO before_id;
            END IF;
        END IF;
        IF final_snapshot IS NOT NULL THEN
            INSERT INTO public.build_history_states(build_id, build_revision, schema_version, snapshot)
            VALUES (pending.build_id, final_revision, 1, final_snapshot) RETURNING id INTO after_id;
        END IF;
        action := CASE WHEN before_id IS NULL THEN 'create' WHEN after_id IS NULL THEN 'delete'
                       ELSE pending.context->>'operation' END;
        INSERT INTO public.build_edit_history(build_id, operation_id, operation, source,
            actor_account_id, actor_system, correlation_kind, correlation_id, before_state_id, after_state_id)
        VALUES (pending.build_id, (pending.context->>'operation_id')::uuid, action, pending.context->>'source',
            (pending.context->>'actor_account_id')::integer, pending.context->>'actor_system',
            pending.context->>'correlation_kind', pending.context->>'correlation_id', before_id, after_id);
    END IF;
    DELETE FROM public.build_edit_pending WHERE transaction_id = pending.transaction_id AND build_id = pending.build_id;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER build_edit_finalize AFTER INSERT ON public.build_edit_pending DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.finish_build_edit_trigger();

CREATE TRIGGER builds_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER builds_reject_truncate BEFORE TRUNCATE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER doors_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER doors_reject_truncate BEFORE TRUNCATE ON public.doors FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER extenders_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER extenders_reject_truncate BEFORE TRUNCATE ON public.extenders FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER other_builds_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.other_builds FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER other_builds_reject_truncate BEFORE TRUNCATE ON public.other_builds FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_tag_assignments_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER build_tag_assignments_reject_truncate BEFORE TRUNCATE ON public.build_tag_assignments FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_creators_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER build_creators_reject_truncate BEFORE TRUNCATE ON public.build_creators FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_versions_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER build_versions_reject_truncate BEFORE TRUNCATE ON public.build_versions FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_links_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER build_links_reject_truncate BEFORE TRUNCATE ON public.build_links FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_source_messages_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_source_messages FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER build_source_messages_reject_truncate BEFORE TRUNCATE ON public.build_source_messages FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_schematics_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_schematics FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER build_schematics_reject_truncate BEFORE TRUNCATE ON public.build_schematics FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER creator_aliases_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.creator_aliases FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER creator_aliases_reject_truncate BEFORE TRUNCATE ON public.creator_aliases FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER versions_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER versions_reject_truncate BEFORE TRUNCATE ON public.versions FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


CREATE TRIGGER utilities_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.utilities FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER utilities_reject_truncate BEFORE TRUNCATE ON public.utilities FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER entrances_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.entrances FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
CREATE TRIGGER entrances_reject_truncate BEFORE TRUNCATE ON public.entrances FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();
"""

_DOWNGRADE = r"""
DROP TRIGGER utilities_capture_edit ON public.utilities;
DROP TRIGGER utilities_reject_truncate ON public.utilities;
DROP TRIGGER entrances_capture_edit ON public.entrances;
DROP TRIGGER entrances_reject_truncate ON public.entrances;
DROP TRIGGER builds_capture_edit ON public.builds;
DROP TRIGGER builds_reject_truncate ON public.builds;
DROP TRIGGER doors_capture_edit ON public.doors;
DROP TRIGGER doors_reject_truncate ON public.doors;
DROP TRIGGER extenders_capture_edit ON public.extenders;
DROP TRIGGER extenders_reject_truncate ON public.extenders;
DROP TRIGGER other_builds_capture_edit ON public.other_builds;
DROP TRIGGER other_builds_reject_truncate ON public.other_builds;
DROP TRIGGER build_tag_assignments_capture_edit ON public.build_tag_assignments;
DROP TRIGGER build_tag_assignments_reject_truncate ON public.build_tag_assignments;
DROP TRIGGER build_creators_capture_edit ON public.build_creators;
DROP TRIGGER build_creators_reject_truncate ON public.build_creators;
DROP TRIGGER build_versions_capture_edit ON public.build_versions;
DROP TRIGGER build_versions_reject_truncate ON public.build_versions;
DROP TRIGGER build_links_capture_edit ON public.build_links;
DROP TRIGGER build_links_reject_truncate ON public.build_links;
DROP TRIGGER build_source_messages_capture_edit ON public.build_source_messages;
DROP TRIGGER build_source_messages_reject_truncate ON public.build_source_messages;
DROP TRIGGER build_schematics_capture_edit ON public.build_schematics;
DROP TRIGGER build_schematics_reject_truncate ON public.build_schematics;
DROP TRIGGER creator_aliases_capture_edit ON public.creator_aliases;
DROP TRIGGER creator_aliases_reject_truncate ON public.creator_aliases;
DROP TRIGGER versions_capture_edit ON public.versions;
DROP TRIGGER versions_reject_truncate ON public.versions;
DROP TRIGGER build_edit_finalize ON public.build_edit_pending;
DROP FUNCTION public.finish_build_edit_trigger();
DROP FUNCTION public.capture_build_edit_trigger();
DROP FUNCTION public.capture_build_edit(bigint);
DROP FUNCTION public.close_build_edit();
DROP FUNCTION public.begin_build_edit(jsonb);
DROP TABLE public.build_edit_pending;
"""


def upgrade() -> None:
    op.execute(_UPGRADE)


def downgrade() -> None:
    op.execute(_DOWNGRADE)
