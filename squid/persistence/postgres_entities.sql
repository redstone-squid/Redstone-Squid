-- Functions and triggers owned by Alembic. Edit this file, then autogenerate a revision.
-- Queue triggers record background work in the same transaction as the source change.
-- Workers later read those queues to update search documents, Discord messages, and records.
-- Other functions enforce invariants that must hold even when writes bypass application services.

CREATE FUNCTION public.delete_vote_session_after_build_vote_session_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- A build session has exactly one subtype row (vote_sessions_kind_subtype_check), so losing
    -- it, usually through the cascade from a deleted build, leaves a session nothing can show.
    -- When the delete cascaded from vote_sessions itself, the parent is already gone.
    DELETE FROM public.vote_sessions WHERE id = OLD.vote_session_id AND kind = 'build';
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.get_quantified_version_names() RETURNS TABLE(id smallint, quantified_name text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        v.id,
        v.edition || ' ' ||
        v.major_version || '.' ||
        v.minor_version || '.' ||
        v.patch_number AS quantified_name
    FROM
        versions v;
END;
$$;

CREATE FUNCTION public.power_set_max(txt text[], max_k integer DEFAULT 8) RETURNS SETOF text[]
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    AS $$
DECLARE
    n     int := array_length(txt, 1);
    mask  int;
BEGIN
    IF n IS NULL OR n = 0 THEN
        RETURN NEXT ARRAY[]::text[];
        RETURN;
    END IF;

    FOR mask IN 0 .. (1 << n) - 1 LOOP
        -- skip masks with more than max_k bits set
        IF (
             SELECT COUNT(*)
             FROM   generate_series(0, n - 1) g
             WHERE  ((mask >> g) & 1) = 1
           ) > max_k THEN
            CONTINUE;
        END IF;

        RETURN NEXT coalesce(                      -- fall back to empty array
            (SELECT array_agg(txt[i] ORDER BY i)
             FROM generate_subscripts(txt, 1) AS i
             WHERE (mask >> (i - 1)) & 1 = 1),
            ARRAY[]::text[]
        );
    END LOOP;
END;
$$;


-- Replacing queued work clears its claim token so an older worker cannot acknowledge the new request.
CREATE FUNCTION public.enqueue_build_search_projection() RETURNS trigger
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

    INSERT INTO public.search_projection_queue
        (resource_kind, source_key, action, enqueued_at, available_at)
    VALUES ('build', target_build_id::text, target_action, now(), now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        attempts = 0,
        claimed_at = NULL,
        claim_token = NULL,
        dead_at = NULL,
        last_error = NULL;

    IF target_kind IN ('door', 'extender') THEN
        INSERT INTO public.record_recompute_queue
            (scope_key, build_kind, build_id, reasons, enqueued_at, available_at)
        VALUES (
            target_kind,
            target_kind,
            CASE WHEN TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE' THEN NULL ELSE target_build_id END,
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
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
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

    INSERT INTO public.search_projection_queue
        (resource_kind, source_key, action, enqueued_at, available_at)
    VALUES ('metadata', target_kind || ':' || target_id::text, target_action, now(), now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        attempts = 0,
        claimed_at = NULL,
        claim_token = NULL,
        dead_at = NULL,
        last_error = NULL;

    IF TG_TABLE_NAME IN ('tag_definitions', 'tag_aliases') THEN
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        SELECT 'build', assignment.build_id::text, 'upsert', now(), now()
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert',
            enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0,
            claimed_at = NULL,
            claim_token = NULL,
            dead_at = NULL,
            last_error = NULL;

        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        SELECT
            'build', assignment.build_id::text, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action,
            generation = EXCLUDED.generation,
            enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            claimed_at = NULL,
            claim_token = NULL,
            dead_at = NULL,
            attempts = 0,
            last_error = NULL;
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        SELECT 'build', bc.build_id::text, 'upsert', now(), now()
        FROM public.build_creators bc
        WHERE bc.alias_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        SELECT 'build', bv.build_id::text, 'upsert', now(), now()
        FROM public.build_versions bv
        WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_result_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'title_results' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        VALUES (
            'record',
            'result:' || target_result_id::text,
            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
            now(),
            now()
        )
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'title_holders' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END;
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        VALUES ('record', 'result:' || target_result_id::text, 'upsert', now(), now())
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSE
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        SELECT 'record', 'result:' || rr.id::text, 'upsert', now(), now()
        FROM public.title_results rr
        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_discord_sync() RETURNS trigger
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
        (resource_kind, source_key, action, generation, enqueued_at, available_at,
         claimed_at, claim_token, dead_at, attempts, last_error)
    VALUES (
        target_kind, target_key::text, target_action,
        nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
    )
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        generation = EXCLUDED.generation,
        enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        claimed_at = NULL,
        claim_token = NULL,
        dead_at = NULL,
        attempts = 0,
        last_error = NULL;

    -- A review card embeds the build it is voting on, so a build change makes the
    -- session's cards stale too. Their posts are keyed by session, not by build, so
    -- the sessions have to be enqueued in their own right.
    IF target_kind = 'build' AND target_action = 'refresh' THEN
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        SELECT
            'vote_session', bvs.vote_session_id::text, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
        FROM public.build_vote_sessions bvs
        WHERE bvs.build_id = target_key
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation,
            enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            claimed_at = NULL,
            claim_token = NULL,
            dead_at = NULL,
            attempts = 0,
            last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_starboard_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_key text;
BEGIN
    IF TG_TABLE_NAME = 'starboards' THEN
        -- A configuration change restyles or re-thresholds every entry on the board.
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        SELECT
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
        FROM public.starboard_entries e
        WHERE e.starboard_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
        RETURN NULL;
    END IF;

    IF TG_TABLE_NAME = 'messages' THEN
        target_key := NULL;
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        SELECT
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
        FROM public.starboard_entries e
        WHERE e.origin_message_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
        RETURN NULL;
    END IF;

    IF TG_OP = 'DELETE' THEN
        target_key := OLD.starboard_id || ':' || OLD.origin_message_id;
    ELSE
        target_key := NEW.starboard_id || ':' || NEW.origin_message_id;
    END IF;
    INSERT INTO public.discord_sync_queue
        (resource_kind, source_key, action, generation, enqueued_at, available_at,
         claimed_at, claim_token, dead_at, attempts, last_error)
    VALUES (
        'starboard_entry', target_key, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'refresh' END,
        nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
    )
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action, generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.publish_domain_event(
    event_type_input text,
    schema_version_input integer,
    aggregate_kind_input text,
    aggregate_id_input bigint,
    payload_input jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_event_id bigint;
BEGIN
    IF event_type_input = '' OR aggregate_kind_input = '' OR schema_version_input < 1 THEN
        RAISE EXCEPTION 'invalid domain event envelope';
    END IF;

    INSERT INTO public.domain_events
        (event_type, schema_version, aggregate_kind, aggregate_id, payload, occurred_at)
    VALUES (
        event_type_input,
        schema_version_input,
        aggregate_kind_input,
        aggregate_id_input,
        coalesce(payload_input, '{}'::jsonb),
        now()
    )
    RETURNING id INTO new_event_id;

    INSERT INTO public.domain_event_deliveries
        (event_id, consumer, available_at, claimed_at, attempts, last_error)
    SELECT new_event_id, consumers.name, now(), NULL, 0, NULL
    FROM public.domain_event_consumers AS consumers;

    -- PostgreSQL delivers NOTIFY only after this transaction commits. It is a
    -- latency hint; durable consumers still poll domain_event_deliveries.
    PERFORM pg_notify('squid_domain_events', new_event_id::text);
    RETURN new_event_id;
END;
$$;

CREATE FUNCTION public.emit_domain_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_type text;
    target_kind text;
    target_id bigint;
    target_payload jsonb;
    target_schema_version integer := 1;
BEGIN
    -- Unlike discord_sync_queue, this log records transitions, so an UPDATE that
    -- rewrites a column to the value it already held must not produce an event.
    IF TG_TABLE_NAME = 'builds' THEN
        IF TG_OP = 'INSERT' THEN
            target_kind := 'build';
            target_id := NEW.id;
            target_type := 'build.submitted';
            target_schema_version := 2;
            target_payload := jsonb_build_object(
                'status', NEW.submission_status,
                'submitter_account_id', NEW.submitter_account_id,
                'category', NEW.category
            );
            PERFORM public.publish_domain_event(
                target_type, target_schema_version, target_kind, target_id, target_payload
            );
            RETURN NULL;
        END IF;
        IF OLD.submission_status IS NOT DISTINCT FROM NEW.submission_status THEN RETURN NULL; END IF;
        target_kind := 'build';
        target_id := NEW.id;
        target_schema_version := 3;
        IF NEW.submission_status = 1 THEN
            target_type := 'build.confirmed';
        ELSIF NEW.submission_status = 2 THEN
            target_type := 'build.denied';
        ELSE
            RETURN NULL;
        END IF;
        target_payload := jsonb_build_object(
            'previous_status', OLD.submission_status,
            'status', NEW.submission_status,
            'submitter_account_id', NEW.submitter_account_id,
            'category', NEW.category,
            'first_confirmation', NEW.submission_status = 1 AND NOT EXISTS (
                SELECT 1
                FROM public.domain_events
                WHERE aggregate_kind = 'build'
                  AND aggregate_id = NEW.id
                  AND event_type = 'build.confirmed'
            )
        );
    ELSE
        IF OLD.status IS NOT DISTINCT FROM NEW.status OR NEW.status <> 'closed' THEN RETURN NULL; END IF;
        target_kind := 'vote_session';
        target_id := NEW.id;
        target_type := 'vote_session.closed';
        target_payload := jsonb_build_object('kind', NEW.kind, 'result', NEW.result);
    END IF;

    PERFORM public.publish_domain_event(
        target_type, target_schema_version, target_kind, target_id, target_payload
    );

    RETURN NULL;
END;
$$;

-- Deferred triggers allow the parent and subtype rows to be written in either order within a transaction.
CREATE FUNCTION public.enforce_vote_session_kind_subtype() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_ids bigint[];
    target_id bigint;
    session_kind text;
    build_count integer;
    delete_log_count integer;
    generic_count integer;
BEGIN
    IF TG_TABLE_NAME = 'vote_sessions' THEN
        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END];
        IF TG_OP = 'UPDATE' AND OLD.id IS DISTINCT FROM NEW.id THEN
            target_ids := array_append(target_ids, OLD.id);
        END IF;
    ELSE
        target_ids := ARRAY[
            CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END
        ];
        IF TG_OP = 'UPDATE' AND OLD.vote_session_id IS DISTINCT FROM NEW.vote_session_id THEN
            target_ids := array_append(target_ids, OLD.vote_session_id);
        END IF;
    END IF;

    FOREACH target_id IN ARRAY target_ids LOOP
        -- The foreign keys take KEY SHARE locks while inserting subtype rows. NO KEY
        -- UPDATE stays compatible with those locks while serializing the final checks
        -- of two transactions that target the same vote session.
        SELECT kind
        INTO session_kind
        FROM public.vote_sessions
        WHERE id = target_id
        FOR NO KEY UPDATE;

        IF NOT FOUND THEN
            CONTINUE;
        END IF;

        SELECT
            (SELECT count(*) FROM public.build_vote_sessions WHERE vote_session_id = target_id),
            (SELECT count(*) FROM public.delete_log_vote_sessions WHERE vote_session_id = target_id),
            (SELECT count(*) FROM public.generic_vote_sessions WHERE vote_session_id = target_id)
        INTO build_count, delete_log_count, generic_count;

        IF NOT (
            (session_kind = 'build' AND build_count = 1 AND delete_log_count = 0 AND generic_count = 0)
            OR (session_kind = 'delete_log' AND build_count = 0 AND delete_log_count = 1 AND generic_count = 0)
            OR (session_kind = 'generic' AND build_count = 0 AND delete_log_count = 0 AND generic_count = 1)
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = format(
                    'vote session %s kind %s has subtype counts build=%s, delete_log=%s, generic=%s',
                    target_id,
                    session_kind,
                    build_count,
                    delete_log_count,
                    generic_count
                ),
                SCHEMA = 'public',
                TABLE = 'vote_sessions',
                CONSTRAINT = 'vote_sessions_kind_subtype_check';
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;

CREATE TRIGGER build_vote_sessions_delete_vote_session AFTER DELETE ON public.build_vote_sessions FOR EACH ROW EXECUTE FUNCTION public.delete_vote_session_after_build_vote_session_delete();


CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

-- Lease and embedding writes change nothing a reader can see, so they must not queue work.
-- Listing the ignored columns makes any new column count as content.
CREATE TRIGGER builds_enqueue_search_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN ((to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at']) IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])) EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER doors_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER extenders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_tag_assignments_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_creators_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER door_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER extender_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER tag_definitions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_definitions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER tag_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER creator_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.creator_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER title_results_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_results FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

CREATE TRIGGER title_holders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_holders FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

CREATE TRIGGER record_computation_runs_enqueue_search AFTER INSERT OR DELETE OR UPDATE OF is_active ON public.record_computation_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

CREATE TRIGGER builds_enqueue_discord_sync AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

-- Lease and embedding writes change nothing a reader can see, so they must not queue work.
-- Listing the ignored columns makes any new column count as content.
CREATE TRIGGER builds_enqueue_discord_sync_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN ((to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at']) IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])) EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER doors_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER extenders_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER build_tag_assignments_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER build_versions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER build_creators_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER build_links_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER door_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER extender_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER vote_sessions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER votes_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.votes FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER starboard_entries_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE OF score ON public.starboard_entries FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();

CREATE TRIGGER starboards_enqueue_discord_sync AFTER UPDATE ON public.starboards FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();

CREATE TRIGGER messages_enqueue_starboard_sync AFTER UPDATE OF deleted_at ON public.messages FOR EACH ROW WHEN (OLD.deleted_at IS DISTINCT FROM NEW.deleted_at) EXECUTE FUNCTION public.enqueue_starboard_sync();

CREATE TRIGGER builds_emit_domain_event AFTER INSERT OR UPDATE OF submission_status ON public.builds FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();

CREATE TRIGGER vote_sessions_emit_domain_event AFTER UPDATE OF status ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();

CREATE CONSTRAINT TRIGGER vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

CREATE CONSTRAINT TRIGGER build_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.build_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

CREATE CONSTRAINT TRIGGER delete_log_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.delete_log_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

CREATE CONSTRAINT TRIGGER generic_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.generic_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

CREATE FUNCTION public.enforce_build_category_subtype() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_ids bigint[];
    target_id bigint;
    build_category text;
    expected_table text;
    door_count integer;
    extender_count integer;
    utility_count integer;
    entrance_count integer;
    other_count integer;
BEGIN
    -- Each build has exactly one row in the subtype table named by its category, which is what
    -- SQLAlchemy's joined-table inheritance needs to load it. Deferred, because the ORM inserts
    -- the builds row before the subtype row.
    IF TG_TABLE_NAME = 'builds' THEN
        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END];
        IF TG_OP = 'UPDATE' AND OLD.id IS DISTINCT FROM NEW.id THEN
            target_ids := array_append(target_ids, OLD.id);
        END IF;
    ELSE
        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END];
        IF TG_OP = 'UPDATE' AND OLD.build_id IS DISTINCT FROM NEW.build_id THEN
            target_ids := array_append(target_ids, OLD.build_id);
        END IF;
    END IF;

    FOREACH target_id IN ARRAY target_ids LOOP
        -- See enforce_vote_session_kind_subtype for why NO KEY UPDATE.
        SELECT category INTO build_category FROM public.builds WHERE id = target_id FOR NO KEY UPDATE;
        IF NOT FOUND THEN
            CONTINUE;
        END IF;

        SELECT
            (SELECT count(*) FROM public.doors WHERE build_id = target_id),
            (SELECT count(*) FROM public.extenders WHERE build_id = target_id),
            (SELECT count(*) FROM public.utilities WHERE build_id = target_id),
            (SELECT count(*) FROM public.entrances WHERE build_id = target_id),
            (SELECT count(*) FROM public.other_builds WHERE build_id = target_id)
        INTO door_count, extender_count, utility_count, entrance_count, other_count;

        expected_table := CASE build_category
            WHEN 'Door' THEN 'doors'
            WHEN 'Extender' THEN 'extenders'
            WHEN 'Utility' THEN 'utilities'
            WHEN 'Entrance' THEN 'entrances'
            WHEN 'Other' THEN 'other_builds'
        END;

        IF NOT (
            door_count + extender_count + utility_count + entrance_count + other_count = 1
            AND CASE expected_table
                WHEN 'doors' THEN door_count
                WHEN 'extenders' THEN extender_count
                WHEN 'utilities' THEN utility_count
                WHEN 'entrances' THEN entrance_count
                WHEN 'other_builds' THEN other_count
            END = 1
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = format(
                    'build %s category %s has subtype counts doors=%s, extenders=%s, utilities=%s, '
                    'entrances=%s, other_builds=%s',
                    target_id, build_category, door_count, extender_count, utility_count, entrance_count,
                    other_count
                ),
                SCHEMA = 'public',
                TABLE = 'builds',
                CONSTRAINT = 'builds_category_subtype_check';
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER builds_category_subtype_check AFTER INSERT OR UPDATE OF id, category ON public.builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE CONSTRAINT TRIGGER doors_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.doors DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE CONSTRAINT TRIGGER extenders_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.extenders DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE CONSTRAINT TRIGGER utilities_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.utilities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE CONSTRAINT TRIGGER entrances_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.entrances DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE CONSTRAINT TRIGGER other_builds_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.other_builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE FUNCTION public.bump_permission_epoch() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_version bigint;
BEGIN
    -- Statement-level, so a bulk grant bumps once rather than per row.
    UPDATE public.permission_epoch
    SET version = version + 1, updated_at = now()
    WHERE id = 1
    RETURNING version INTO new_version;

    -- Same contract as squid_domain_events: NOTIFY arrives only after commit and
    -- is a latency hint. Watchers poll permission_epoch as the durable signal, so
    -- a dropped notification costs latency rather than correctness.
    --
    -- The singleton row is created by the migration that installs this trigger,
    -- so a missing one means someone deleted it. Skip the notify rather than
    -- error: a permission write failing outright would be far worse than a stale
    -- cache, and the watchers' wall-clock backstop still bounds the staleness.
    IF new_version IS NOT NULL THEN
        PERFORM pg_notify('squid_permissions', new_version::text);
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER permission_grants_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_grants FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

CREATE TRIGGER permission_role_assignments_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_role_assignments FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

CREATE TRIGGER permission_roles_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_roles FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

CREATE TRIGGER permission_role_patterns_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_role_patterns FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

CREATE TRIGGER permission_role_includes_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_role_includes FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();

CREATE FUNCTION public.enqueue_submission_status() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    run_key uuid;
    draft_key uuid;
    target_kind text;
    target_key text;
BEGIN
    IF TG_TABLE_NAME = 'submission_inference_runs' THEN
        IF (CASE WHEN TG_OP = 'DELETE' THEN OLD.inputs ELSE NEW.inputs END)->>'purpose' != 'submission' THEN
            RETURN NULL;
        END IF;
        run_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSIF TG_TABLE_NAME = 'submission_drafts' THEN
        draft_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        run_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.inference_run_id ELSE NEW.inference_run_id END;
    ELSE
        draft_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.draft_id ELSE NEW.draft_id END;
        SELECT inference_run_id INTO run_key FROM public.submission_drafts WHERE id = draft_key;
    END IF;
    FOR target_kind, target_key IN
        SELECT 'submission_draft', draft_key::text WHERE draft_key IS NOT NULL
        UNION ALL SELECT 'inference_run', run_key::text WHERE run_key IS NOT NULL
    LOOP
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        VALUES (target_kind, target_key, 'refresh', nextval('public.discord_sync_generation_seq'),
                now(), now(), NULL, NULL, NULL, 0, NULL)
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action, generation = EXCLUDED.generation,
            enqueued_at = EXCLUDED.enqueued_at, available_at = EXCLUDED.available_at,
            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE TRIGGER submission_runs_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_inference_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();

CREATE TRIGGER submission_drafts_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_drafts FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();

CREATE TRIGGER submission_intake_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_supplied_attachments FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();

CREATE FUNCTION public.reject_submission_finalization_input_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'check_violation',
        MESSAGE = 'submission finalization inputs are immutable',
        SCHEMA = 'public',
        TABLE = 'submission_finalization_inputs';
END;
$$;

CREATE TRIGGER submission_finalization_inputs_immutable BEFORE UPDATE ON public.submission_finalization_inputs FOR EACH ROW EXECUTE FUNCTION public.reject_submission_finalization_input_update();

CREATE FUNCTION public.check_permission_role_include_cycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Serialize graph checks; the next query gets a fresh READ COMMITTED snapshot.
    -- Repeatable-read snapshots cannot see an edge committed while waiting.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'role inclusion writes require READ COMMITTED isolation'
            USING ERRCODE = '0A000';
    END IF;
    -- A guild role must not change what a global role grants, and one guild's role must not
    -- change another guild's. Global roles may be included anywhere.
    IF EXISTS (
        SELECT 1
        FROM permission_roles role, permission_roles included
        WHERE role.id = NEW.role_id AND included.id = NEW.included_role_id
          AND included.guild_id IS NOT NULL AND included.guild_id IS DISTINCT FROM role.guild_id
    ) THEN
        RAISE EXCEPTION 'permission role may only include global roles or roles of its own guild'
            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_scope';
    END IF;
    PERFORM pg_advisory_xact_lock(1936812391, 1);
    IF EXISTS (
        WITH RECURSIVE reachable(id) AS (
            SELECT NEW.included_role_id
            UNION
            SELECT edge.included_role_id
            FROM permission_role_includes edge JOIN reachable ON edge.role_id = reachable.id
        )
        SELECT 1 FROM reachable WHERE id = NEW.role_id
    ) THEN
        RAISE EXCEPTION 'permission role inclusion cycle'
            USING ERRCODE = '23514', CONSTRAINT = 'permission_role_includes_acyclic';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER permission_role_includes_acyclic AFTER INSERT OR UPDATE ON public.permission_role_includes FOR EACH ROW EXECUTE FUNCTION public.check_permission_role_include_cycle();
