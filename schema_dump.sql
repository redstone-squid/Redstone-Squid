--
-- PostgreSQL database dump
--

\restrict 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

-- Dumped from database version 17.8 (Debian 17.8-1.pgdg12+1)
-- Dumped by pg_dump version 17.8 (Debian 17.8-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: unaccent; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;


--
-- Name: EXTENSION unaccent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION unaccent IS 'text search dictionary that removes accents';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: bump_permission_epoch(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: delete_orphaned_build_vote_sessions_after_builds_delete(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM vote_sessions
    WHERE id IN (
        SELECT vote_sessions.id
        FROM vote_sessions vs
        LEFT JOIN build_vote_sessions bvs ON vs.id = bvs.vote_session_id
        LEFT JOIN delete_log_vote_sessions dvs ON vs.id = dvs.vote_session_id
        WHERE bvs.vote_session_id IS NULL AND dvs.vote_session_id IS NULL
    );
    RETURN NULL; -- Statement-level triggers do not use OLD or NEW
END;
$$;


--
-- Name: emit_domain_event(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: enforce_vote_session_kind_subtype(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: enqueue_build_search_projection(); Type: FUNCTION; Schema: public; Owner: -
--

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
        locked_at = NULL,
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
            locked_at = NULL,
            claim_token = NULL,
            last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: enqueue_computed_record_search_projection(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_result_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'record_results' THEN
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
            attempts = 0, locked_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'record_result_holders' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END;
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        VALUES ('record', 'result:' || target_result_id::text, 'upsert', now(), now())
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, locked_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSE
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        SELECT 'record', 'result:' || rr.id::text, 'upsert', now(), now()
        FROM public.record_results rr
        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, locked_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: enqueue_discord_sync(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: enqueue_metadata_search_projection(); Type: FUNCTION; Schema: public; Owner: -
--

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
        locked_at = NULL,
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
            locked_at = NULL,
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
            attempts = 0, locked_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        SELECT 'build', bv.build_id::text, 'upsert', now(), now()
        FROM public.build_versions bv
        WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, locked_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: enqueue_starboard_sync(); Type: FUNCTION; Schema: public; Owner: -
--

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

    IF TG_TABLE_NAME = 'starboard_origin_messages' THEN
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


--
-- Name: get_quantified_version_names(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: idempotency_requests_mirror_caller(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.idempotency_requests_mirror_caller() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.caller IS NULL THEN
        NEW.caller := NEW.principal;
    ELSIF NEW.principal IS NULL THEN
        NEW.principal := NEW.caller;
    ELSIF NEW.caller <> NEW.principal THEN
        RAISE EXCEPTION 'idempotency caller vocabulary columns disagree'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: power_set_max(text[], integer); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: publish_domain_event(text, integer, text, bigint, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.publish_domain_event(event_type_input text, schema_version_input integer, aggregate_kind_input text, aggregate_id_input bigint, payload_input jsonb DEFAULT '{}'::jsonb) RETURNS bigint
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


--
-- Name: set_locked_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_locked_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  if new.is_locked then
    new.locked_at := now();
  else
    new.locked_at := null;
  end if;
  return new;
end;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: account_identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_identities (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    provider text NOT NULL,
    subject text NOT NULL,
    display_name text,
    verified_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_public boolean DEFAULT true NOT NULL,
    avatar_key text,
    CONSTRAINT account_identities_provider_check CHECK ((provider = ANY (ARRAY['discord'::text, 'java'::text, 'bedrock'::text]))),
    CONSTRAINT account_identities_subject_check CHECK (((subject = btrim(subject)) AND (subject <> ''::text)))
);


--
-- Name: TABLE account_identities; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.account_identities IS 'A verified provider subject attached to exactly one account.';


--
-- Name: COLUMN account_identities.is_public; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_identities.is_public IS 'Whether this identity appears on the account''s public creator profile.';


--
-- Name: COLUMN account_identities.avatar_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_identities.avatar_key IS 'Provider rendering key, where the subject alone is not enough to build an avatar URL.

Discord avatar URLs need the hash, which only the gateway supplies. Java heads derive from
the UUID, so this stays NULL there.';


--
-- Name: account_identities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.account_identities ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.account_identities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: account_merge_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_merge_tickets (
    account_id integer NOT NULL,
    code_digest text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT account_merge_tickets_expiry_after_creation CHECK ((expires_at > created_at))
);


--
-- Name: TABLE account_merge_tickets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.account_merge_tickets IS 'A live, single-use claim that one account consents to being absorbed by another.

A merge needs recent proof of *both* accounts, and no one session can hold both. The ticket is
that second proof, carried through the only channel the two sides share: a person who can sign
into each. Minting one is the absorbed side authenticating; redeeming it is the surviving side
doing so, inside the ticket''s lifetime.

Keyed on the account rather than on the digest, so minting replaces. One account can only ever
have one live ticket, which is most of why an eight-character code is enough.';


--
-- Name: COLUMN account_merge_tickets.code_digest; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_merge_tickets.code_digest IS 'Digest, never the code. The plaintext is shown once at mint time and never stored, exactly
as a verification code is.';


--
-- Name: COLUMN account_merge_tickets.expires_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_merge_tickets.expires_at IS 'Doubles as the proof timestamp: a ticket is redeemable for exactly as long as
`RecentAccountProof` accepts the authentication that minted it.';


--
-- Name: account_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_profiles (
    account_id integer NOT NULL,
    display_name text,
    bio text,
    pronouns text,
    links jsonb DEFAULT '[]'::jsonb NOT NULL,
    hidden boolean DEFAULT false NOT NULL,
    avatar_identity_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT account_profiles_bio_length CHECK (((bio IS NULL) OR ((char_length(bio) >= 1) AND (char_length(bio) <= 500)))),
    CONSTRAINT account_profiles_display_name_length CHECK (((display_name IS NULL) OR ((char_length(display_name) >= 1) AND (char_length(display_name) <= 64)))),
    CONSTRAINT account_profiles_display_name_trimmed CHECK (((display_name IS NULL) OR (display_name = btrim(display_name)))),
    CONSTRAINT account_profiles_links_shape CHECK (((jsonb_typeof(links) = 'array'::text) AND (jsonb_array_length(links) <= 10))),
    CONSTRAINT account_profiles_pronouns_length CHECK (((pronouns IS NULL) OR ((char_length(pronouns) >= 1) AND (char_length(pronouns) <= 40))))
);


--
-- Name: TABLE account_profiles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.account_profiles IS 'What an account chooses to publish about itself on its creator page.

A child of `accounts` rather than more columns on it. The account row is an identity anchor
that thirty-odd foreign keys point at and that link and merge paths lock `FOR UPDATE`;
profile text is user-edited prose with an entirely different write cadence, and widening the
anchor to carry it would make every identity read pay for a bio.';


--
-- Name: COLUMN account_profiles.display_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.display_name IS 'Presentation only, deliberately not a `creator_aliases` name: renaming yourself here moves
no build credit and needs no staff review.';


--
-- Name: COLUMN account_profiles.links; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.links IS 'External links as `[{"label": ..., "url": ...}]`.

JSONB rather than a child table: nothing queries links, and every write replaces the whole
list from one owner, so a table would buy referential integrity to nothing and cost a join on
the hottest public read.';


--
-- Name: COLUMN account_profiles.hidden; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.hidden IS 'Whether to withhold the profile. A hidden profile still serves its aliases and build
credits, because a creator page that vanished would strand every build crediting it.';


--
-- Name: COLUMN account_profiles.avatar_identity_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.avatar_identity_id IS 'The linked identity this profile''s avatar is rendered from.

`SET NULL` so unlinking that identity clears the avatar rather than leaving a render pointing
at a subject we no longer hold. Ownership — that the identity belongs to this same account —
is checked in the repository, since the composite foreign key that would enforce it here
cannot coexist with `ON DELETE SET NULL`.';


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    consent_version text,
    consented_at timestamp with time zone,
    public_creator_id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT accounts_consent_receipt_complete CHECK (((consent_version IS NULL) = (consented_at IS NULL)))
);


--
-- Name: TABLE accounts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.accounts IS 'An internal caller independent of every external identity provider.';


--
-- Name: accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.accounts_id_seq OWNED BY public.accounts.id;


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id bigint NOT NULL,
    key_id text NOT NULL,
    secret_hash bytea NOT NULL,
    label text NOT NULL,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    owner_account_id integer,
    created_by_account_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    last_used_at timestamp with time zone,
    last_used_ip inet
);


--
-- Name: TABLE api_keys; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.api_keys IS 'A revocable high-entropy credential used by an API service client.';


--
-- Name: COLUMN api_keys.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.id IS 'Internal identifier unrelated to the public token key ID.';


--
-- Name: COLUMN api_keys.key_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.key_id IS 'Public lookup portion of the API token.';


--
-- Name: COLUMN api_keys.secret_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.secret_hash IS 'HMAC-SHA256 digest of the unrecoverable token secret.

Keyed by a deployment pepper that never reaches this database; see
`docs/credential-hashing.md`.';


--
-- Name: COLUMN api_keys.label; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.label IS 'Human-readable description of the credential''s owner or purpose.';


--
-- Name: COLUMN api_keys.scopes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.scopes IS 'Capabilities granted to this credential.';


--
-- Name: COLUMN api_keys.owner_account_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.owner_account_id IS 'Optional account responsible for the credential.';


--
-- Name: COLUMN api_keys.created_by_account_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.created_by_account_id IS 'Account that created the credential, when known.';


--
-- Name: COLUMN api_keys.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.created_at IS 'When the credential was created.';


--
-- Name: COLUMN api_keys.expires_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.expires_at IS 'Optional instant after which authentication is rejected.';


--
-- Name: COLUMN api_keys.revoked_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.revoked_at IS 'When the credential was revoked, or ``None`` while active.';


--
-- Name: COLUMN api_keys.last_used_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.last_used_at IS 'Most recent throttled usage timestamp.';


--
-- Name: COLUMN api_keys.last_used_ip; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.last_used_ip IS 'IP address associated with the most recent recorded use.';


--
-- Name: api_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.api_keys ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.api_keys_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: build_creators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_creators (
    build_id bigint NOT NULL,
    alias_id integer NOT NULL
);


--
-- Name: TABLE build_creators; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_creators IS 'Association table between builds and the creator names credited on them.';


--
-- Name: build_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_links (
    build_id bigint NOT NULL,
    url text NOT NULL,
    media_type text
);


--
-- Name: TABLE build_links; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_links IS 'A link associated with a build (image, video, world download).';


--
-- Name: build_schematics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_schematics (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    file_sha256 text NOT NULL,
    is_primary boolean NOT NULL,
    original_filename text,
    width integer NOT NULL,
    height integer NOT NULL,
    length integer NOT NULL,
    allocated_width integer NOT NULL,
    allocated_height integer NOT NULL,
    allocated_length integer NOT NULL,
    block_count integer NOT NULL,
    bounding_volume bigint NOT NULL,
    entity_count integer NOT NULL,
    palette_size integer NOT NULL,
    region_names text[] NOT NULL,
    source_data_version integer,
    declared_name text,
    declared_author text,
    signs jsonb NOT NULL,
    fingerprint_structural text,
    fingerprint_shape text,
    fingerprint_exact text,
    signature_structural text,
    analyzer_version text NOT NULL,
    analysis_schema_version smallint NOT NULL,
    lattice jsonb,
    analyzed_at timestamp with time zone DEFAULT now() NOT NULL,
    simulation_evidence jsonb,
    visibility text DEFAULT 'legacy_unverified'::text NOT NULL,
    license_code text,
    rights_attested_at timestamp with time zone,
    rights_attested_by_account_id integer,
    published_at timestamp with time zone,
    withdrawn_at timestamp with time zone,
    uploaded_by_account_id integer,
    CONSTRAINT build_schematics_license_check CHECK (((license_code IS NULL) OR (license_code = ANY (ARRAY['cc0_1_0'::text, 'cc_by_4_0'::text, 'cc_by_sa_4_0'::text, 'cc_by_nd_4_0'::text, 'cc_by_nc_4_0'::text, 'cc_by_nc_sa_4_0'::text, 'cc_by_nc_nd_4_0'::text])))),
    CONSTRAINT build_schematics_publication_complete CHECK (((visibility <> 'public_download'::text) OR ((license_code IS NOT NULL) AND (rights_attested_at IS NOT NULL) AND (rights_attested_by_account_id IS NOT NULL)))),
    CONSTRAINT build_schematics_visibility_check CHECK ((visibility = ANY (ARRAY['legacy_unverified'::text, 'reviewer_only'::text, 'public_download'::text])))
);


--
-- Name: TABLE build_schematics; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_schematics IS 'One analyzed schematic attached to a build.

Metrics and fingerprints are denormalised onto this row so duplicate shortlisting is a
plain indexed query. Fingerprints are only comparable within the `analyzer_version` that
produced them, so every identity index carries that column and every lookup filters on it;
an engine upgrade therefore becomes a visible backfill rather than a silent regression.';


--
-- Name: COLUMN build_schematics.file_sha256; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.file_sha256 IS 'The stored bytes this analysis describes. `RESTRICT` because several builds can share
one file and losing it would strand every analysis that references it.';


--
-- Name: COLUMN build_schematics.is_primary; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.is_primary IS 'Whether this is the schematic shown on the build card and used for duplicate checks.';


--
-- Name: COLUMN build_schematics.original_filename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.original_filename IS 'The name the uploader''s file had, kept for display only. Never trusted for typing.';


--
-- Name: COLUMN build_schematics.width; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.width IS 'Tight content width. Machine-read, unlike the human-declared value on `builds`.';


--
-- Name: COLUMN build_schematics.allocated_width; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.allocated_width IS 'Width of the region the file allocates, which can far exceed the tight content.';


--
-- Name: COLUMN build_schematics.block_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.block_count IS 'Non-air block count. **Not** the Door Rules cumulative volume, which counts air pockets
and carries hallway, frame, and hitbox exceptions no static read can apply.';


--
-- Name: COLUMN build_schematics.bounding_volume; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.bounding_volume IS 'Tight bounding box volume including air. Materialised so it can be range-scanned.';


--
-- Name: COLUMN build_schematics.palette_size; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.palette_size IS 'How many distinct block states the file declares.';


--
-- Name: COLUMN build_schematics.source_data_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.source_data_version IS 'The Minecraft data version the file declares, or `None` when it declares none.';


--
-- Name: COLUMN build_schematics.signs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.signs IS 'Sign text recovered from the schematic, as `{x, y, z, text}` objects.';


--
-- Name: COLUMN build_schematics.fingerprint_structural; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.fingerprint_structural IS 'Coarse translation-invariant bucket. A build differing by a single block still matches,
so this is a pre-filter feeding pairwise ranking and never a duplicate verdict by itself.';


--
-- Name: COLUMN build_schematics.fingerprint_shape; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.fingerprint_shape IS 'Translation- and rotation-invariant identity. This is the primary duplicate index.';


--
-- Name: COLUMN build_schematics.fingerprint_exact; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.fingerprint_exact IS 'Material- and orientation-sensitive identity, the strict tier.';


--
-- Name: COLUMN build_schematics.signature_structural; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.signature_structural IS 'The engine''s structural signature document, kept for pre-filter experiments.';


--
-- Name: COLUMN build_schematics.analyzer_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.analyzer_version IS 'Which engine build produced the fingerprints on this row, e.g. `nucleation-0.9.2`.';


--
-- Name: COLUMN build_schematics.analysis_schema_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.analysis_schema_version IS 'Which revision of *our* analysis produced this row, bumped when we change what we read.';


--
-- Name: COLUMN build_schematics.lattice; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.lattice IS 'The highest-coverage repeating unit cell found, if the build has one.';


--
-- Name: COLUMN build_schematics.simulation_evidence; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.simulation_evidence IS 'Staff-triggered tick-engine evidence. It never changes the build''s declared timing.';


--
-- Name: COLUMN build_schematics.visibility; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.visibility IS 'Explicit download choice. Existing attachments remain private until re-attested.';


--
-- Name: COLUMN build_schematics.uploaded_by_account_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.uploaded_by_account_id IS 'Who supplied the file, beside `rights_attested_by_account_id` so the table
carries one attribution style rather than two.';


--
-- Name: build_schematics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.build_schematics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: build_schematics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.build_schematics_id_seq OWNED BY public.build_schematics.id;


--
-- Name: build_source_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_source_messages (
    build_id bigint NOT NULL,
    message_id bigint NOT NULL,
    "position" smallint NOT NULL
);


--
-- Name: TABLE build_source_messages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_source_messages IS 'Association table between builds and the Discord messages they came from.

Many-to-many in both directions: one submission can span a body message plus
follow-up images, and one build-log message can yield several builds at once.
The message side is RESTRICT because a message row is a retained fact that
outlives the builds referring to it; deleting a build only drops the link.';


--
-- Name: COLUMN build_source_messages."position"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_source_messages."position" IS 'Submission order, so the first message stays identifiable as the request itself.';


--
-- Name: build_tag_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_tag_assignments (
    build_id bigint NOT NULL,
    tag_id bigint NOT NULL,
    value_type text NOT NULL,
    numeric_value numeric,
    text_value text,
    boolean_value boolean,
    display_unit_key text,
    display_order smallint,
    evidence text,
    provenance text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_account_id integer,
    CONSTRAINT build_tag_assignments_finite_numeric_check CHECK (((numeric_value IS NULL) OR ((numeric_value)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])))),
    CONSTRAINT build_tag_assignments_order_check CHECK (((display_order IS NULL) OR (display_order >= 0))),
    CONSTRAINT build_tag_assignments_provenance_check CHECK ((provenance = ANY (ARRAY['submitted'::text, 'inferred'::text, 'moderated'::text, 'legacy_import'::text]))),
    CONSTRAINT build_tag_assignments_typed_value_check CHECK ((((value_type = 'none'::text) AND (num_nonnulls(numeric_value, text_value, boolean_value) = 0)) OR ((value_type = 'numeric'::text) AND (numeric_value IS NOT NULL) AND (num_nonnulls(text_value, boolean_value) = 0)) OR ((value_type = 'text'::text) AND (text_value IS NOT NULL) AND (num_nonnulls(numeric_value, boolean_value) = 0)) OR ((value_type = 'boolean'::text) AND (boolean_value IS NOT NULL) AND (num_nonnulls(numeric_value, text_value) = 0))))
);


--
-- Name: TABLE build_tag_assignments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_tag_assignments IS 'A typed tag value attached to one build.';


--
-- Name: build_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_versions (
    build_id bigint NOT NULL,
    version_id smallint NOT NULL
);


--
-- Name: TABLE build_versions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_versions IS 'Association table between builds and their versions.';


--
-- Name: build_vote_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_vote_sessions (
    vote_session_id bigint NOT NULL,
    build_id bigint NOT NULL,
    changes jsonb NOT NULL
);


--
-- Name: builds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.builds (
    id bigint NOT NULL,
    submission_status smallint NOT NULL,
    edited_time timestamp with time zone DEFAULT now() NOT NULL,
    record_category text,
    extra_info jsonb DEFAULT '{}'::jsonb NOT NULL,
    width integer,
    height integer,
    depth integer,
    completion_time text,
    submission_time timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    category text,
    ai_generated boolean NOT NULL,
    version_spec text,
    embedding public.vector(1536),
    is_locked boolean DEFAULT false NOT NULL,
    locked_at timestamp with time zone,
    completion_at timestamp with time zone,
    completion_evidence text,
    description text,
    submitter_account_id integer NOT NULL,
    lock_token uuid,
    lock_expires_at timestamp with time zone,
    revision bigint DEFAULT 1 NOT NULL,
    display_name text,
    source_submission_draft_id uuid,
    sponsor_installation_id uuid,
    sponsor_display_name text,
    sponsor_address text,
    sponsor_description text,
    sponsor_website_url text,
    CONSTRAINT builds_display_name_valid CHECK (((display_name IS NULL) OR ((display_name = btrim(display_name)) AND (display_name <> ''::text) AND (char_length(display_name) <= 120)))),
    CONSTRAINT builds_lock_lease_complete CHECK (((is_locked AND (lock_token IS NOT NULL) AND (lock_expires_at IS NOT NULL)) OR ((NOT is_locked) AND (lock_token IS NULL) AND (lock_expires_at IS NULL)))),
    CONSTRAINT builds_revision_positive CHECK ((revision > 0)),
    CONSTRAINT builds_sponsor_address_length CHECK (((sponsor_address IS NULL) OR ((char_length(sponsor_address) >= 1) AND (char_length(sponsor_address) <= 255)))),
    CONSTRAINT builds_sponsor_description_length CHECK (((sponsor_description IS NULL) OR ((char_length(sponsor_description) >= 1) AND (char_length(sponsor_description) <= 500)))),
    CONSTRAINT builds_sponsor_display_name_length CHECK (((sponsor_display_name IS NULL) OR ((char_length(sponsor_display_name) >= 1) AND (char_length(sponsor_display_name) <= 80)))),
    CONSTRAINT builds_sponsor_projection_complete CHECK (((sponsor_installation_id IS NOT NULL) OR ((sponsor_display_name IS NULL) AND (sponsor_address IS NULL) AND (sponsor_description IS NULL) AND (sponsor_website_url IS NULL)))),
    CONSTRAINT builds_sponsor_website_valid CHECK (((sponsor_website_url IS NULL) OR (((char_length(sponsor_website_url) >= 1) AND (char_length(sponsor_website_url) <= 2048)) AND (sponsor_website_url ~ '^https?://'::text)))),
    CONSTRAINT check_record_category CHECK ((record_category = ANY (ARRAY['Smallest'::text, 'Fastest'::text, 'First'::text, 'Smallest Fastest'::text, 'Fastest Smallest'::text, NULL::text]))),
    CONSTRAINT check_status CHECK ((submission_status = ANY (ARRAY[0, 1, 2]))),
    CONSTRAINT submissions_build_depth_check CHECK ((depth > 0)),
    CONSTRAINT submissions_build_height_check CHECK ((height > 0)),
    CONSTRAINT submissions_build_width_check CHECK ((width > 0))
);


--
-- Name: TABLE builds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.builds IS 'A build submitted by a user.';


--
-- Name: COLUMN builds.embedding; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.builds.embedding IS 'Application-owned semantic vector stored in the authoritative build row.';


--
-- Name: COLUMN builds.source_submission_draft_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.builds.source_submission_draft_id IS 'Stable finalization key retained even if the short-lived source draft is later pruned.';


--
-- Name: COLUMN builds.sponsor_installation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.builds.sponsor_installation_id IS 'Opaque Paper installation ID snapshotted without a mutable foreign key.';


--
-- Name: cli_device_enrollments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cli_device_enrollments (
    id uuid NOT NULL,
    device_code_hash bytea NOT NULL,
    user_code_hash bytea NOT NULL,
    public_key bytea NOT NULL,
    client_instance_id uuid NOT NULL,
    label text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    approved_by_account_id integer,
    approved_at timestamp with time zone,
    exchanged_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT cli_device_enrollments_approval_complete CHECK (((approved_by_account_id IS NULL) = (approved_at IS NULL))),
    CONSTRAINT cli_device_enrollments_device_hash_length CHECK ((octet_length(device_code_hash) = 32)),
    CONSTRAINT cli_device_enrollments_exchange_requires_approval CHECK (((exchanged_at IS NULL) OR (approved_at IS NOT NULL))),
    CONSTRAINT cli_device_enrollments_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT cli_device_enrollments_label_length CHECK (((char_length(label) >= 1) AND (char_length(label) <= 80))),
    CONSTRAINT cli_device_enrollments_public_key_length CHECK ((octet_length(public_key) = 32)),
    CONSTRAINT cli_device_enrollments_user_hash_length CHECK ((octet_length(user_code_hash) = 32))
);


--
-- Name: TABLE cli_device_enrollments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cli_device_enrollments IS 'A browser-approved enrollment storing only digests of bearer codes.';


--
-- Name: cli_devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cli_devices (
    id uuid NOT NULL,
    account_id integer NOT NULL,
    public_key bytea NOT NULL,
    client_instance_id uuid NOT NULL,
    label text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT cli_devices_label_length CHECK (((char_length(label) >= 1) AND (char_length(label) <= 80))),
    CONSTRAINT cli_devices_last_used_after_creation CHECK ((last_used_at >= created_at)),
    CONSTRAINT cli_devices_public_key_length CHECK ((octet_length(public_key) = 32))
);


--
-- Name: TABLE cli_devices; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cli_devices IS 'An account-owned Ed25519 CLI device.';


--
-- Name: cli_session_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cli_session_challenges (
    id uuid NOT NULL,
    device_id uuid NOT NULL,
    nonce_hash bytea NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    CONSTRAINT cli_session_challenges_consumed_after_creation CHECK (((consumed_at IS NULL) OR (consumed_at >= created_at))),
    CONSTRAINT cli_session_challenges_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT cli_session_challenges_nonce_hash_length CHECK ((octet_length(nonce_hash) = 32))
);


--
-- Name: TABLE cli_session_challenges; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cli_session_challenges IS 'A one-time device proof nonce stored only as a digest.';


--
-- Name: cli_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cli_sessions (
    id uuid NOT NULL,
    device_id uuid NOT NULL,
    token_hash bytea NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT cli_sessions_expiry_after_issue CHECK ((expires_at > issued_at)),
    CONSTRAINT cli_sessions_last_seen_after_issue CHECK ((last_seen_at >= issued_at)),
    CONSTRAINT cli_sessions_token_hash_length CHECK ((octet_length(token_hash) = 32))
);


--
-- Name: TABLE cli_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cli_sessions IS 'A short-lived CLI bearer session storing only its token digest.';


--
-- Name: creator_alias_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.creator_alias_claims (
    id integer NOT NULL,
    alias_id integer NOT NULL,
    account_id integer NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by_account_id integer,
    CONSTRAINT creator_alias_claims_resolution_complete CHECK (((status = 'pending'::text) = (resolved_at IS NULL))),
    CONSTRAINT creator_alias_claims_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text])))
);


--
-- Name: TABLE creator_alias_claims; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.creator_alias_claims IS 'An account''s request to be credited under a creator alias.';


--
-- Name: creator_alias_claims_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.creator_alias_claims_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: creator_alias_claims_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.creator_alias_claims_id_seq OWNED BY public.creator_alias_claims.id;


--
-- Name: creator_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.creator_aliases (
    id integer NOT NULL,
    name text NOT NULL,
    normalized_name text NOT NULL,
    account_id integer,
    claimed_at timestamp with time zone,
    claim_method text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT creator_aliases_claim_complete CHECK (((account_id IS NULL) = (claimed_at IS NULL))),
    CONSTRAINT creator_aliases_claim_method_check CHECK (((claim_method IS NULL) OR (claim_method = ANY (ARRAY['verified_ign'::text, 'staff_approved'::text, 'migrated'::text])))),
    CONSTRAINT creator_aliases_claim_method_complete CHECK (((account_id IS NULL) = (claim_method IS NULL))),
    CONSTRAINT creator_aliases_normalized_name_folded CHECK (((normalized_name = btrim(normalized_name)) AND (normalized_name !~ '[A-Z]'::text)))
);


--
-- Name: TABLE creator_aliases; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.creator_aliases IS 'A creator name credited on a build, optionally claimed by an account.';


--
-- Name: creator_aliases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.creator_aliases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: creator_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.creator_aliases_id_seq OWNED BY public.creator_aliases.id;


--
-- Name: delete_log_vote_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.delete_log_vote_sessions (
    vote_session_id bigint NOT NULL,
    target_message_id bigint NOT NULL,
    target_channel_id bigint NOT NULL,
    target_server_id bigint NOT NULL
);


--
-- Name: discord_posts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.discord_posts (
    message_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    resource_kind text NOT NULL,
    resource_key text NOT NULL,
    surface text NOT NULL,
    applied_revision bigint DEFAULT 0 NOT NULL,
    posted_at timestamp with time zone DEFAULT now() NOT NULL,
    rendered_at timestamp with time zone,
    suppressed_at timestamp with time zone,
    CONSTRAINT discord_posts_applied_revision_check CHECK ((applied_revision >= 0)),
    CONSTRAINT discord_posts_resource_kind_check CHECK ((resource_kind = ANY (ARRAY['build'::text, 'vote_session'::text, 'starboard_entry'::text]))),
    CONSTRAINT discord_posts_surface_check CHECK ((surface = ANY (ARRAY['build_card'::text, 'build_review'::text, 'vote_card'::text, 'starboard_entry'::text])))
);


--
-- Name: TABLE discord_posts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.discord_posts IS 'A Discord message the bot owns and keeps rendered for some resource.

Holds only applied state. What the post *should* look like lives in the matching
`discord_sync_queue` row, so staleness is `applied_revision < queue.generation`
rather than a desired revision copied onto every post by a trigger.';


--
-- Name: COLUMN discord_posts.channel_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.channel_id IS 'Denormalised from `messages.channel_id`: a unique index cannot span a join.';


--
-- Name: COLUMN discord_posts.applied_revision; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.applied_revision IS 'The queue generation this post was last rendered at.';


--
-- Name: COLUMN discord_posts.suppressed_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.suppressed_at IS 'Set when the post was deleted outside the bot. Renderers choose whether to repost.';


--
-- Name: discord_sync_generation_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.discord_sync_generation_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: discord_sync_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.discord_sync_queue (
    id bigint NOT NULL,
    resource_kind text NOT NULL,
    source_key text NOT NULL,
    action text DEFAULT 'refresh'::text NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    dead_at timestamp with time zone,
    generation bigint DEFAULT nextval('public.discord_sync_generation_seq'::regclass) NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT discord_sync_queue_action_check CHECK ((action = ANY (ARRAY['refresh'::text, 'delete'::text]))),
    CONSTRAINT discord_sync_queue_resource_kind_check CHECK ((resource_kind = ANY (ARRAY['build'::text, 'vote_session'::text, 'starboard_entry'::text])))
);


--
-- Name: TABLE discord_sync_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.discord_sync_queue IS 'A coalesced request to refresh one Discord-rendered resource.

Not an event queue, despite the shape. Rows are coalesced on
`(resource_kind, source_key)` by the six `INSERT ... ON CONFLICT ... DO
UPDATE` triggers in `squid/persistence/postgres_entities.sql` — that is where
the coalescing actually happens — deleted on acknowledgement, and carry a
`generation` compared against a post''s applied revision as a staleness token
rather than read as an ordering. An event log would be append-only and
replayable; this table is neither. A row means "this resource''s Discord posts
are stale", and re-reading it tells you what the resource should look like
now, not what happened to it.

Discord-specific on purpose: every row exists to repair a Discord post, and
the only consumer renders them (`squid/bot/sync/reconciler.py`). The
application layer speaks of *reconciliation* rather than *sync* because a
sync names a transfer, and nothing here transfers anything.';


--
-- Name: COLUMN discord_sync_queue.generation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_sync_queue.generation IS 'A globally monotonic staleness token, not a per-row counter.

Acknowledging a job deletes its queue row, so a counter restarted at 1 on the next
enqueue and could name a revision below one a post had already applied. Sequence
values survive that because they are never rolled back or reused.';


--
-- Name: COLUMN discord_sync_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_sync_queue.available_at IS 'When this row next becomes claimable, and the only column backoff writes.

Kept separate from `enqueued_at` so a repeatedly failing row keeps its place in
FIFO order and still reports its true age to the queue-health gauges.';


--
-- Name: COLUMN discord_sync_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_sync_queue.claim_token IS 'The database-minted fencing token handed to the worker that claimed this row.

Nullable for now so code from the previous release, which stamps only
`claimed_at`, can keep draining the queue across a deploy window.';


--
-- Name: discord_sync_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.discord_sync_queue ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.discord_sync_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: domain_event_consumers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.domain_event_consumers (
    name text NOT NULL
);


--
-- Name: TABLE domain_event_consumers; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.domain_event_consumers IS 'A registered reader of the event log.';


--
-- Name: domain_event_deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.domain_event_deliveries (
    event_id bigint NOT NULL,
    consumer text NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    dead_at timestamp with time zone,
    claim_token uuid,
    claim_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT domain_event_deliveries_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT domain_event_deliveries_claim_count_nonnegative CHECK ((claim_count >= 0))
);


--
-- Name: TABLE domain_event_deliveries; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.domain_event_deliveries IS 'One consumer''s outstanding delivery of one event.';


--
-- Name: domain_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.domain_events (
    id bigint NOT NULL,
    event_type text NOT NULL,
    aggregate_kind text NOT NULL,
    aggregate_id bigint NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    schema_version smallint DEFAULT 1 NOT NULL,
    CONSTRAINT domain_events_schema_version_positive CHECK ((schema_version > 0))
);


--
-- Name: TABLE domain_events; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.domain_events IS 'One state transition, recorded once and never coalesced.';


--
-- Name: domain_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.domain_events ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.domain_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: door_timing_variants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.door_timing_variants (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    label text DEFAULT 'default'::text NOT NULL,
    opening_time bigint,
    visible_opening_time bigint,
    closing_time bigint,
    visible_closing_time bigint,
    opening_reset_time bigint,
    closing_reset_time bigint
);


--
-- Name: TABLE door_timing_variants; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.door_timing_variants IS 'A measured door timing variant used for lexicographic fastest records.';


--
-- Name: door_timing_variants_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.door_timing_variants ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.door_timing_variants_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: doors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doors (
    build_id bigint NOT NULL,
    orientation text NOT NULL,
    door_width integer NOT NULL,
    door_height integer NOT NULL,
    door_depth integer,
    normal_opening_time bigint,
    normal_closing_time bigint,
    visible_opening_time bigint,
    visible_closing_time bigint
);


--
-- Name: TABLE doors; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doors IS 'A door build with specific dimensions and timing information.';


--
-- Name: entrances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entrances (
    build_id bigint NOT NULL
);


--
-- Name: TABLE entrances; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.entrances IS 'An entrance build.';


--
-- Name: error_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.error_reports (
    id uuid NOT NULL,
    correlation_id text NOT NULL,
    reference text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    surface text NOT NULL,
    origin text,
    exception_type text NOT NULL,
    message text NOT NULL,
    error_code text,
    traceback text NOT NULL,
    context jsonb NOT NULL,
    log_tail jsonb NOT NULL,
    work_lost boolean DEFAULT false NOT NULL
);


--
-- Name: TABLE error_reports; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.error_reports IS 'One unexpected failure, retained so the reference its user was shown can be resolved.';


--
-- Name: COLUMN error_reports.correlation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.correlation_id IS 'The full correlation ID, as it appears in logs and the Request-Id response header.';


--
-- Name: COLUMN error_reports.reference; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.reference IS 'The shortened form shown to the user, indexed because it is what they quote back.

Not unique: it is a 48-bit prefix of the correlation ID, so a collision is possible even
though it is vanishingly unlikely, and a unique constraint would turn that into a failure to
record the second error rather than an ambiguous lookup.';


--
-- Name: COLUMN error_reports.occurred_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.occurred_at IS 'When the failure was captured.';


--
-- Name: COLUMN error_reports.expires_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.expires_at IS 'When retention drops this report.';


--
-- Name: COLUMN error_reports.surface; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.surface IS 'Which transport failed: an application command, a view callback, a route, a worker job.';


--
-- Name: COLUMN error_reports.origin; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.origin IS 'The command name, route, or job the failure came from, when the surface knows it.';


--
-- Name: COLUMN error_reports.exception_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.exception_type IS 'Qualified name of the raised exception class.';


--
-- Name: COLUMN error_reports.message; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.message IS 'The exception''s own string form, which is never shown to the user who triggered it.';


--
-- Name: COLUMN error_reports.error_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.error_code IS 'The application ErrorCode, when the failure carried one.';


--
-- Name: COLUMN error_reports.traceback; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.traceback IS 'Rendered traceback, truncated from the front so the frames nearest the failure survive.';


--
-- Name: COLUMN error_reports.context; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.context IS 'Redacted diagnostic context. Never contains stable Discord account identifiers.';


--
-- Name: COLUMN error_reports.log_tail; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.log_tail IS 'What the process logged under this correlation ID before failing, oldest first.';


--
-- Name: COLUMN error_reports.work_lost; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.work_lost IS 'Whether this failure permanently abandoned work, as a dead-lettered job does.';


--
-- Name: extender_timing_variants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extender_timing_variants (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    label text DEFAULT 'default'::text NOT NULL,
    retraction_time bigint,
    extension_time bigint,
    retraction_reset_time bigint,
    extension_reset_time bigint
);


--
-- Name: TABLE extender_timing_variants; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.extender_timing_variants IS 'A measured piston-extender timing variant used for fastest records.';


--
-- Name: extender_timing_variants_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.extender_timing_variants ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.extender_timing_variants_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: extenders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extenders (
    build_id bigint NOT NULL,
    orientation text,
    extension_length integer,
    extender_type text
);


--
-- Name: TABLE extenders; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.extenders IS 'An extender build.';


--
-- Name: generic_vote_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.generic_vote_sessions (
    vote_session_id bigint NOT NULL,
    guild_id bigint,
    question text NOT NULL,
    visibility text NOT NULL,
    deadline timestamp with time zone NOT NULL,
    scope text DEFAULT 'guild'::text NOT NULL,
    CONSTRAINT generic_vote_sessions_network_guild_check CHECK (((scope = 'guild'::text) OR (guild_id IS NOT NULL))),
    CONSTRAINT generic_vote_sessions_scope_check CHECK ((scope = ANY (ARRAY['guild'::text, 'network'::text]))),
    CONSTRAINT generic_vote_sessions_visibility_check CHECK ((visibility = ANY (ARRAY['anonymous_live'::text, 'visible_live'::text, 'anonymous_hidden'::text])))
);


--
-- Name: TABLE generic_vote_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.generic_vote_sessions IS 'Metadata for a user-created generic poll.';


--
-- Name: COLUMN generic_vote_sessions.guild_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.generic_vote_sessions.guild_id IS 'The guild whose emoji palette the poll was drafted against.

Nullable so a poll can be created by a transport that has no guild -- the REST API
or a standalone draft -- and have its presentation messages attached afterwards.';


--
-- Name: COLUMN generic_vote_sessions.scope; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.generic_vote_sessions.scope IS 'Whether the poll is carded only in its own guild or in every vote channel.';


--
-- Name: guild_vote_emojis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_vote_emojis (
    guild_id bigint NOT NULL,
    kind text NOT NULL,
    identifier text NOT NULL,
    emoji text NOT NULL,
    choice text NOT NULL,
    label text,
    "position" smallint NOT NULL,
    CONSTRAINT guild_vote_emojis_choice_check CHECK ((choice = ANY (ARRAY['approve'::text, 'deny'::text, 'generic'::text]))),
    CONSTRAINT guild_vote_emojis_kind_check CHECK ((kind = ANY (ARRAY['build'::text, 'delete_log'::text, 'generic'::text])))
);


--
-- Name: TABLE guild_vote_emojis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.guild_vote_emojis IS 'One ordered emoji in a guild/session-kind preset.';


--
-- Name: guild_vote_role_weights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.guild_vote_role_weights (
    guild_id bigint NOT NULL,
    kind text NOT NULL,
    role_id bigint NOT NULL,
    multiplier double precision NOT NULL,
    CONSTRAINT guild_vote_role_weights_kind_check CHECK ((kind = ANY (ARRAY['build'::text, 'delete_log'::text, 'generic'::text]))),
    CONSTRAINT guild_vote_role_weights_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision)))
);


--
-- Name: TABLE guild_vote_role_weights; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.guild_vote_role_weights IS 'A role multiplier scoped to one guild and session kind.';


--
-- Name: idempotency_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.idempotency_requests (
    id uuid NOT NULL,
    principal text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint bytea NOT NULL,
    method text NOT NULL,
    route text NOT NULL,
    state text DEFAULT 'in_progress'::text NOT NULL,
    response_status smallint,
    response_headers jsonb,
    response_body_ciphertext bytea,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    response_body_key_id text,
    response_body_nonce bytea,
    caller text NOT NULL,
    CONSTRAINT idempotency_requests_method_check CHECK ((method = ANY (ARRAY['POST'::text, 'PUT'::text, 'PATCH'::text, 'DELETE'::text]))),
    CONSTRAINT idempotency_requests_response_state_check CHECK ((((state = 'in_progress'::text) AND (response_status IS NULL) AND (response_headers IS NULL) AND (response_body_ciphertext IS NULL) AND (response_body_key_id IS NULL) AND (response_body_nonce IS NULL) AND (completed_at IS NULL)) OR ((state = 'completed'::text) AND (response_status IS NOT NULL) AND (response_headers IS NOT NULL) AND (response_body_ciphertext IS NOT NULL) AND (response_body_key_id IS NOT NULL) AND (response_body_nonce IS NOT NULL) AND (completed_at IS NOT NULL)))),
    CONSTRAINT idempotency_requests_state_check CHECK ((state = ANY (ARRAY['in_progress'::text, 'completed'::text])))
);


--
-- Name: TABLE idempotency_requests; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.idempotency_requests IS 'One caller-scoped mutation reservation and its completed HTTP response.';


--
-- Name: COLUMN idempotency_requests.principal; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.idempotency_requests.principal IS 'The caller namespace a key is reserved in.

The application layer calls this the *caller*; the column keeps the older
word because renaming it needs a migration, a rewrite of the unique index it
anchors, and a redeploy window, for a name no client ever sees. The same
trade applies to the `principal` partition in `RateLimit-Policy` and to
`SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`, both of which deployments and
clients can observe. If the ban is meant repo-wide, that is its own commit.';


--
-- Name: COLUMN idempotency_requests.caller; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.idempotency_requests.caller IS 'The server-derived caller namespace a key is reserved in.';


--
-- Name: media_artifact_objects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_artifact_objects (
    object_key text NOT NULL,
    sha256 text NOT NULL,
    byte_size bigint NOT NULL,
    first_upload_id uuid NOT NULL,
    last_upload_id uuid NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    deleted_at timestamp with time zone,
    cleanup_claimed_at timestamp with time zone,
    cleanup_claim_token uuid,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_artifact_objects_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT media_artifact_objects_cleanup_claim_shape CHECK (((cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL))),
    CONSTRAINT media_artifact_objects_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT media_artifact_objects_size_positive CHECK ((byte_size > 0))
);


--
-- Name: TABLE media_artifact_objects; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_artifact_objects IS 'Durable lifecycle and cleanup audit state for one content-addressed object.';


--
-- Name: media_artifact_publications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_artifact_publications (
    object_key text NOT NULL,
    upload_id uuid NOT NULL,
    claim_token uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    renewed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_artifact_publications_expiry_after_creation CHECK ((expires_at > created_at))
);


--
-- Name: TABLE media_artifact_publications; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_artifact_publications IS 'A crash-recoverable lease protecting one claim''s in-flight object publication.';


--
-- Name: media_artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_artifacts (
    id bigint NOT NULL,
    upload_id uuid NOT NULL,
    role text NOT NULL,
    object_key text NOT NULL,
    content_type text NOT NULL,
    byte_size bigint NOT NULL,
    sha256 text NOT NULL,
    width integer,
    height integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_artifacts_dimensions_by_role CHECK ((((role = 'report'::text) AND (width IS NULL) AND (height IS NULL)) OR ((role <> 'report'::text) AND (width > 0) AND (height > 0)))),
    CONSTRAINT media_artifacts_role_check CHECK ((role = ANY (ARRAY['output'::text, 'poster'::text, 'video_thumbnail'::text, 'report'::text]))),
    CONSTRAINT media_artifacts_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT media_artifacts_size_positive CHECK ((byte_size > 0))
);


--
-- Name: TABLE media_artifacts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_artifacts IS 'Content-addressed normalized output, poster, or disclosure report metadata.';


--
-- Name: media_artifacts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.media_artifacts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.media_artifacts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: media_normalization_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_normalization_jobs (
    upload_id uuid NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    claim_token uuid,
    attempts integer DEFAULT 0 NOT NULL,
    completed_at timestamp with time zone,
    dead_at timestamp with time zone,
    discarded_at timestamp with time zone,
    last_error text,
    CONSTRAINT media_normalization_jobs_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT media_normalization_jobs_state_shape CHECK ((((status = 'pending'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'claimed'::text) AND (claimed_at IS NOT NULL) AND (claim_token IS NOT NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'completed'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NOT NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'dead'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NOT NULL) AND (discarded_at IS NULL)) OR ((status = 'discarded'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NOT NULL)))),
    CONSTRAINT media_normalization_jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'completed'::text, 'dead'::text, 'discarded'::text])))
);


--
-- Name: TABLE media_normalization_jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_normalization_jobs IS 'A retained, claim-token-fenced request to normalize one raw upload.';


--
-- Name: media_uploads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_uploads (
    id uuid NOT NULL,
    draft_id uuid NOT NULL,
    kind text NOT NULL,
    source_content_type text NOT NULL,
    source_byte_size bigint NOT NULL,
    source_sha256 text NOT NULL,
    source_object_key text NOT NULL,
    strip_audio boolean DEFAULT false NOT NULL,
    raw_deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT media_uploads_audio_only_for_video CHECK (((kind = 'video'::text) OR (strip_audio = false))),
    CONSTRAINT media_uploads_content_type_length CHECK (((char_length(source_content_type) >= 1) AND (char_length(source_content_type) <= 255))),
    CONSTRAINT media_uploads_kind_check CHECK ((kind = ANY (ARRAY['image'::text, 'video'::text]))),
    CONSTRAINT media_uploads_source_sha256_check CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT media_uploads_source_size_positive CHECK ((source_byte_size > 0))
);


--
-- Name: TABLE media_uploads; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_uploads IS 'Immutable raw-upload metadata used to verify worker input.';


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    guild_id bigint,
    channel_id bigint,
    id bigint NOT NULL,
    content text,
    author_id bigint NOT NULL,
    created_at timestamp with time zone,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    edited_at timestamp with time zone,
    deleted_at timestamp with time zone
);


--
-- Name: TABLE messages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.messages IS 'A Discord message the bot has seen.

One row per Discord message, holding only what is true about the message itself.
Why it matters is expressed by the tables that reference it: `discord_posts` for
the messages the bot owns and renders, `build_source_messages` for the ones a
build was inferred from.';


--
-- Name: COLUMN messages.guild_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.guild_id IS 'The guild the message was sent in, or NULL in DMs.';


--
-- Name: COLUMN messages.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.id IS 'The Discord snowflake. Never generated here; the message exists before the row.';


--
-- Name: COLUMN messages.content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.content IS 'Never exposed through the API. Retained for offline build inference and edit views.';


--
-- Name: COLUMN messages.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.created_at IS 'When Discord created the message, denormalised from the snowflake.';


--
-- Name: COLUMN messages.observed_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.observed_at IS 'When the bot first recorded this message.';


--
-- Name: COLUMN messages.edited_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.edited_at IS 'When `content` was last refreshed from a Discord edit.';


--
-- Name: COLUMN messages.deleted_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.deleted_at IS 'Set when Discord reports the message gone. The row is a retained fact, never erased.';


--
-- Name: minecraft_paper_installations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.minecraft_paper_installations (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    label text NOT NULL,
    secret_hash bytea NOT NULL,
    credential_version integer DEFAULT 1 NOT NULL,
    public_profile_enabled boolean DEFAULT false NOT NULL,
    public_display_name text,
    public_address text,
    public_description text,
    public_website_url text,
    sponsor_opt_in boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    rotated_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT minecraft_paper_installations_address_length CHECK (((public_address IS NULL) OR ((char_length(public_address) >= 1) AND (char_length(public_address) <= 255)))),
    CONSTRAINT minecraft_paper_installations_description_length CHECK (((public_description IS NULL) OR ((char_length(public_description) >= 1) AND (char_length(public_description) <= 500)))),
    CONSTRAINT minecraft_paper_installations_display_name_length CHECK (((public_display_name IS NULL) OR ((char_length(public_display_name) >= 1) AND (char_length(public_display_name) <= 80)))),
    CONSTRAINT minecraft_paper_installations_label_length CHECK (((char_length(label) >= 1) AND (char_length(label) <= 80))),
    CONSTRAINT minecraft_paper_installations_secret_hash_length CHECK ((octet_length(secret_hash) = 32)),
    CONSTRAINT minecraft_paper_installations_sponsor_requires_public CHECK (((NOT sponsor_opt_in) OR public_profile_enabled)),
    CONSTRAINT minecraft_paper_installations_version_positive CHECK ((credential_version >= 1)),
    CONSTRAINT minecraft_paper_installations_website_length CHECK (((public_website_url IS NULL) OR ((char_length(public_website_url) >= 1) AND (char_length(public_website_url) <= 2048))))
);


--
-- Name: TABLE minecraft_paper_installations; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.minecraft_paper_installations IS 'An account-owned Paper installation with a non-recoverable credential.';


--
-- Name: minecraft_player_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.minecraft_player_challenges (
    id uuid NOT NULL,
    device_code_hash bytea NOT NULL,
    user_code_hash bytea NOT NULL,
    origin text NOT NULL,
    java_uuid uuid NOT NULL,
    installation_id uuid,
    installation_credential_version integer,
    pkce_s256_challenge text,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    approved_by_account_id integer,
    approved_at timestamp with time zone,
    exchanged_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT minecraft_player_challenges_approval_complete CHECK (((approved_by_account_id IS NULL) = (approved_at IS NULL))),
    CONSTRAINT minecraft_player_challenges_device_hash_length CHECK ((octet_length(device_code_hash) = 32)),
    CONSTRAINT minecraft_player_challenges_exchange_requires_approval CHECK (((exchanged_at IS NULL) OR (approved_at IS NOT NULL))),
    CONSTRAINT minecraft_player_challenges_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT minecraft_player_challenges_origin_binding CHECK ((((origin = 'paper'::text) AND (installation_id IS NOT NULL) AND (installation_credential_version IS NOT NULL) AND (pkce_s256_challenge IS NULL)) OR ((origin = 'fabric'::text) AND (installation_id IS NULL) AND (installation_credential_version IS NULL) AND (pkce_s256_challenge IS NOT NULL)))),
    CONSTRAINT minecraft_player_challenges_origin_check CHECK ((origin = ANY (ARRAY['paper'::text, 'fabric'::text]))),
    CONSTRAINT minecraft_player_challenges_pkce_format CHECK (((pkce_s256_challenge IS NULL) OR (pkce_s256_challenge ~ '^[A-Za-z0-9_-]{43}$'::text))),
    CONSTRAINT minecraft_player_challenges_user_hash_length CHECK ((octet_length(user_code_hash) = 32))
);


--
-- Name: TABLE minecraft_player_challenges; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.minecraft_player_challenges IS 'A short-lived device flow storing only hashes of both bearer codes.';


--
-- Name: minecraft_player_grants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.minecraft_player_grants (
    id uuid NOT NULL,
    challenge_id uuid NOT NULL,
    token_hash bytea NOT NULL,
    account_id integer NOT NULL,
    java_uuid uuid NOT NULL,
    origin text NOT NULL,
    installation_id uuid,
    installation_credential_version integer,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT minecraft_player_grants_expiry_after_issue CHECK ((expires_at > issued_at)),
    CONSTRAINT minecraft_player_grants_origin_binding CHECK ((((origin = 'paper'::text) AND (installation_id IS NOT NULL) AND (installation_credential_version IS NOT NULL)) OR ((origin = 'fabric'::text) AND (installation_id IS NULL) AND (installation_credential_version IS NULL)))),
    CONSTRAINT minecraft_player_grants_origin_check CHECK ((origin = ANY (ARRAY['paper'::text, 'fabric'::text]))),
    CONSTRAINT minecraft_player_grants_token_hash_length CHECK ((octet_length(token_hash) = 32))
);


--
-- Name: TABLE minecraft_player_grants; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.minecraft_player_grants IS 'A short-lived origin- and identity-bound player bearer grant.';


--
-- Name: notification_deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_deliveries (
    id bigint NOT NULL,
    notification_id bigint NOT NULL,
    account_id integer NOT NULL,
    generation bigint DEFAULT 1 NOT NULL,
    nonce uuid DEFAULT gen_random_uuid() NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    claim_token uuid,
    attempts integer DEFAULT 0 NOT NULL,
    sent_at timestamp with time zone,
    dead_at timestamp with time zone,
    last_error text,
    CONSTRAINT notification_deliveries_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT notification_deliveries_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT notification_deliveries_generation_positive CHECK ((generation > 0))
);


--
-- Name: TABLE notification_deliveries; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.notification_deliveries IS 'A durable at-least-once Discord DM delivery attempt.';


--
-- Name: notification_deliveries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.notification_deliveries ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.notification_deliveries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: notification_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_profiles (
    account_id integer NOT NULL,
    web_enabled boolean DEFAULT false NOT NULL,
    dm_enabled boolean DEFAULT false NOT NULL,
    dm_suspended_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE notification_profiles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.notification_profiles IS 'Independent notification channel preferences.

Carries no consent receipt: notifications are covered by the one privacy notice, whose
receipt lives on `accounts`. A row here means "these switches", not "this person agreed".';


--
-- Name: notification_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_subscriptions (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    kind text NOT NULL,
    subject_id uuid,
    filter jsonb,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT notification_subscriptions_filter_complete CHECK (((kind = 'record_filter'::text) = (filter IS NOT NULL))),
    CONSTRAINT notification_subscriptions_kind_check CHECK ((kind = ANY (ARRAY['creator'::text, 'record'::text, 'record_filter'::text]))),
    CONSTRAINT notification_subscriptions_subject_complete CHECK (((kind = ANY (ARRAY['creator'::text, 'record'::text])) = (subject_id IS NOT NULL)))
);


--
-- Name: TABLE notification_subscriptions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.notification_subscriptions IS 'A creator, exact record, or structured record-filter subscription.';


--
-- Name: notification_subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.notification_subscriptions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.notification_subscriptions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    event_id bigint NOT NULL,
    source_key text NOT NULL,
    kind text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    web_visible boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    read_at timestamp with time zone,
    CONSTRAINT notifications_kind_check CHECK ((kind = ANY (ARRAY['build_confirmed'::text, 'build_denied'::text, 'creator_build_confirmed'::text, 'record_gained'::text, 'staff_build_submitted'::text])))
);


--
-- Name: TABLE notifications; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.notifications IS 'An idempotently materialized user notification.';


--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.notifications ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.notifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: oauth_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oauth_states (
    state text NOT NULL,
    code_verifier text NOT NULL,
    redirect_to text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    provider text NOT NULL
);


--
-- Name: TABLE oauth_states; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.oauth_states IS 'One-time OAuth PKCE state shared across API replicas.';


--
-- Name: COLUMN oauth_states.provider; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.oauth_states.provider IS 'The namespace this state was minted for; the callback refuses a mismatch.';


--
-- Name: other_builds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.other_builds (
    build_id bigint NOT NULL
);


--
-- Name: TABLE other_builds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.other_builds IS 'A build which does not fit one of the structured catalogue categories.';


--
-- Name: permission_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permission_audit_log (
    id bigint NOT NULL,
    action text NOT NULL,
    actor_account_id integer,
    subject_kind text,
    subject_id bigint,
    subject_guild_id bigint,
    pattern text,
    role_id integer,
    scope_guild_id bigint,
    effect smallint,
    expires_at timestamp with time zone,
    reason text,
    details jsonb,
    at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE permission_audit_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_audit_log IS 'An append-only record of one permission mutation.

Written in the same transaction as the change it describes. The repository
exposes no update or delete path for this table.';


--
-- Name: COLUMN permission_audit_log.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_audit_log.action IS 'What happened: `grant`, `revoke`, `role_create`, `role_pattern_add`, and so on.';


--
-- Name: COLUMN permission_audit_log.reason; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_audit_log.reason IS 'Mandatory for `forbid`, enforced by the service rather than the schema so
the column stays usable for the actions that do not need it.';


--
-- Name: permission_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.permission_audit_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.permission_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: permission_epoch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permission_epoch (
    id smallint NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_epoch_singleton CHECK ((id = 1))
);


--
-- Name: TABLE permission_epoch; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_epoch IS 'A single counter bumped by any permission write.

Three processes each hold their own rule-set cache, so a grant issued in the
API has to become visible in the bot. A trigger bumps this row and sends
`NOTIFY squid_permissions`; watchers treat the notification as a latency hint
and poll this counter as the durable signal.';


--
-- Name: permission_grants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permission_grants (
    id bigint NOT NULL,
    pattern text NOT NULL,
    effect smallint NOT NULL,
    granted_by_account_id integer,
    subject_account_id integer,
    subject_role_id bigint,
    subject_guild_id bigint,
    scope_guild_id bigint,
    expires_at timestamp with time zone,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text,
    CONSTRAINT permission_grants_effect_check CHECK ((effect = ANY (ARRAY[1, '-1'::integer, '-2'::integer]))),
    CONSTRAINT permission_grants_one_subject CHECK ((num_nonnulls(subject_account_id, subject_role_id) = 1)),
    CONSTRAINT permission_grants_role_subject_has_guild CHECK (((subject_role_id IS NULL) OR (subject_guild_id IS NOT NULL))),
    CONSTRAINT permission_grants_role_subject_stays_home CHECK (((subject_role_id IS NULL) OR (scope_guild_id IS NULL) OR (scope_guild_id = subject_guild_id)))
);


--
-- Name: TABLE permission_grants; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_grants IS 'A direct allow, deny or forbid attached to an account or a Discord role.';


--
-- Name: COLUMN permission_grants.granted_by_account_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_grants.granted_by_account_id IS 'Who issued this, or NULL when the system did: a migration backfill or the
owner recovery CLI has no human grantor, and inventing one would put a
fictional account into the audit trail.';


--
-- Name: COLUMN permission_grants.subject_role_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_grants.subject_role_id IS 'A Discord role snowflake, when the rule is attached to a role.';


--
-- Name: COLUMN permission_grants.subject_guild_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_grants.subject_guild_id IS 'The guild the subject Discord role lives in.';


--
-- Name: COLUMN permission_grants.scope_guild_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_grants.scope_guild_id IS 'Where the rule applies, or NULL for everywhere. A guild-scoped rule can
never satisfy a global node; the resolver checks that against the node''s
declared scope, so nodes added later are safe under old grants.';


--
-- Name: permission_grants_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.permission_grants ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.permission_grants_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: permission_role_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permission_role_assignments (
    id bigint NOT NULL,
    role_id integer NOT NULL,
    granted_by_account_id integer,
    subject_account_id integer,
    subject_role_id bigint,
    subject_guild_id bigint,
    scope_guild_id bigint,
    expires_at timestamp with time zone,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text,
    CONSTRAINT permission_role_assignments_one_subject CHECK ((num_nonnulls(subject_account_id, subject_role_id) = 1)),
    CONSTRAINT permission_role_assignments_role_subject_has_guild CHECK (((subject_role_id IS NULL) OR (subject_guild_id IS NOT NULL))),
    CONSTRAINT permission_role_assignments_role_subject_stays_home CHECK (((subject_role_id IS NULL) OR (scope_guild_id IS NULL) OR (scope_guild_id = subject_guild_id)))
);


--
-- Name: TABLE permission_role_assignments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_role_assignments IS 'A role held by an account or by everyone with a Discord role.';


--
-- Name: COLUMN permission_role_assignments.granted_by_account_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_role_assignments.granted_by_account_id IS 'Who issued this, or NULL when the system did. See `PermissionGrant`.';


--
-- Name: permission_role_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.permission_role_assignments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.permission_role_assignments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: permission_role_includes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permission_role_includes (
    role_id integer NOT NULL,
    included_role_id integer NOT NULL,
    added_by_account_id integer,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_role_includes_no_self_include CHECK ((role_id <> included_role_id))
);


--
-- Name: TABLE permission_role_includes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_role_includes IS 'A composition edge: one role including another''s patterns.';


--
-- Name: permission_role_patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permission_role_patterns (
    role_id integer NOT NULL,
    pattern text NOT NULL,
    mode smallint NOT NULL,
    added_by_account_id integer,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_role_patterns_mode_check CHECK ((mode = ANY (ARRAY[1, '-1'::integer])))
);


--
-- Name: TABLE permission_role_patterns; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_role_patterns IS 'One pattern a role includes or subtracts.

Subtraction is not a deny: it withholds the pattern from *this* role''s
contribution, and any other role including it still confers it. That is
Azure''s `NotActions` semantics, and it is why a role can be written as "this
namespace, minus its destructive members" without poisoning other grants.';


--
-- Name: COLUMN permission_role_patterns.pattern; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_role_patterns.pattern IS 'A node name, a `*`/`**` wildcard, or an `@tag` selector.';


--
-- Name: COLUMN permission_role_patterns.mode; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_role_patterns.mode IS '1 to include, -1 to subtract. One mode per pattern, so a role cannot
contradict itself.';


--
-- Name: permission_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permission_roles (
    id integer NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    description text,
    guild_id bigint,
    builtin_key text,
    rank integer DEFAULT 0 NOT NULL,
    protected boolean DEFAULT false NOT NULL,
    created_by_account_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_roles_builtin_is_global CHECK (((builtin_key IS NULL) OR (guild_id IS NULL))),
    CONSTRAINT permission_roles_slug_format CHECK ((slug ~ '^[a-z][a-z0-9-]{1,31}$'::text))
);


--
-- Name: TABLE permission_roles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_roles IS 'A named bundle of permission patterns.

Built-in roles keep their pattern lists in code, so this row exists only to
give assignments a foreign key; any `permission_role_patterns` rows attached
to a built-in are additive overrides on top of the code-defined list.';


--
-- Name: COLUMN permission_roles.slug; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_roles.slug IS 'Stable handle used in commands, unique within the owning guild.';


--
-- Name: COLUMN permission_roles.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_roles.name IS 'Display name.';


--
-- Name: COLUMN permission_roles.guild_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_roles.guild_id IS 'The guild owning this role, or NULL for a global one.';


--
-- Name: COLUMN permission_roles.builtin_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_roles.builtin_key IS 'Identifies a role whose patterns are defined in code rather than here.';


--
-- Name: COLUMN permission_roles.rank; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_roles.rank IS 'Management hierarchy only: who may edit whom. Deliberately absent from
permission resolution, so reordering roles can never change an authorization
outcome. Enforced by property P10.';


--
-- Name: COLUMN permission_roles.protected; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_roles.protected IS 'Refuses structural edits from anyone but the bot owner.';


--
-- Name: permission_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.permission_roles ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.permission_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: public_creator_redirects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.public_creator_redirects (
    retired_public_creator_id uuid NOT NULL,
    target_account_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE public_creator_redirects; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.public_creator_redirects IS 'Permanent redirect from a merged public creator identifier.';


--
-- Name: record_competitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_competitions (
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_class text NOT NULL,
    build_kind text NOT NULL,
    version_scope text NOT NULL,
    version_id smallint,
    category_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT record_competitions_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_competitions_record_class_check CHECK ((record_class = ANY (ARRAY['first'::text, 'fastest'::text, 'smallest'::text, 'fastest_smallest'::text, 'smallest_fastest'::text]))),
    CONSTRAINT record_competitions_version_scope_check CHECK ((version_scope = ANY (ARRAY['all_time'::text, 'current'::text])))
);


--
-- Name: TABLE record_competitions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_competitions IS 'A stable public identity for a logical record competition across rulesets.';


--
-- Name: record_computation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_computation_runs (
    id bigint NOT NULL,
    ruleset_id bigint NOT NULL,
    build_kind text NOT NULL,
    version_id smallint,
    status text DEFAULT 'running'::text NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    error text,
    CONSTRAINT record_computation_runs_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_computation_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: TABLE record_computation_runs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_computation_runs IS 'An immutable attempt to calculate records for one build and version scope.';


--
-- Name: record_computation_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.record_computation_runs ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.record_computation_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: record_definition_facets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_definition_facets (
    definition_id bigint NOT NULL,
    facet_kind text NOT NULL,
    facet_id integer NOT NULL,
    display_order smallint DEFAULT 0 NOT NULL,
    CONSTRAINT record_definition_facets_kind_check CHECK ((facet_kind = ANY (ARRAY['restriction'::text, 'type'::text, 'pattern'::text, 'category'::text])))
);


--
-- Name: TABLE record_definition_facets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_definition_facets IS 'A canonical taxonomy facet belonging to a record definition.';


--
-- Name: record_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_definitions (
    id bigint NOT NULL,
    ruleset_id bigint NOT NULL,
    record_class text NOT NULL,
    build_kind text NOT NULL,
    version_scope text NOT NULL,
    version_id smallint,
    category_key text NOT NULL,
    materialization_source text DEFAULT 'eager'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    title text NOT NULL,
    subtitle text,
    title_diagnostics jsonb DEFAULT '[]'::jsonb NOT NULL,
    competition_id uuid NOT NULL,
    CONSTRAINT record_definitions_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_definitions_materialization_source_check CHECK ((materialization_source = ANY (ARRAY['eager'::text, 'seeded'::text, 'public_lookup'::text]))),
    CONSTRAINT record_definitions_record_class_check CHECK ((record_class = ANY (ARRAY['first'::text, 'fastest'::text, 'smallest'::text, 'fastest_smallest'::text, 'smallest_fastest'::text]))),
    CONSTRAINT record_definitions_version_scope_check CHECK ((version_scope = ANY (ARRAY['all_time'::text, 'current'::text])))
);


--
-- Name: TABLE record_definitions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_definitions IS 'A ruleset-specific definition of one stable record competition.';


--
-- Name: record_definitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.record_definitions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.record_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: record_holder_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_holder_history (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    definition_id bigint NOT NULL,
    build_id bigint NOT NULL,
    predecessor_id bigint,
    held_from timestamp with time zone NOT NULL,
    held_until timestamp with time zone,
    metric_snapshot jsonb NOT NULL,
    CONSTRAINT record_holder_history_interval_check CHECK (((held_until IS NULL) OR (held_until >= held_from)))
);


--
-- Name: TABLE record_holder_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_holder_history IS 'A reconstructed interval in a definition''s beaten-record chronology.';


--
-- Name: record_holder_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.record_holder_history ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.record_holder_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: record_recompute_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_recompute_queue (
    id bigint NOT NULL,
    scope_key text NOT NULL,
    build_kind text NOT NULL,
    build_id bigint,
    reasons jsonb DEFAULT '[]'::jsonb NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    locked_at timestamp with time zone,
    last_error text,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT record_recompute_queue_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text])))
);


--
-- Name: TABLE record_recompute_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_recompute_queue IS 'A durable request to recompute an affected record scope.';


--
-- Name: COLUMN record_recompute_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.record_recompute_queue.available_at IS 'When this row next becomes claimable, and the only column backoff writes.';


--
-- Name: COLUMN record_recompute_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.record_recompute_queue.claim_token IS 'The database-minted fencing token handed to the worker that leased this scope.

Scopes are leased in batches and acknowledged as a set, so the token is what
tells a finishing worker''s acknowledgement from work enqueued during its run.';


--
-- Name: record_recompute_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.record_recompute_queue ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.record_recompute_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: record_result_holders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_result_holders (
    result_id bigint NOT NULL,
    build_id bigint NOT NULL,
    rank smallint DEFAULT 1 NOT NULL,
    metric_snapshot jsonb NOT NULL,
    title text NOT NULL,
    subtitle text,
    completion_at timestamp with time zone,
    CONSTRAINT record_result_holders_rank_check CHECK ((rank > 0))
);


--
-- Name: TABLE record_result_holders; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_result_holders IS 'A co-holder of a resolved computed record.';


--
-- Name: record_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_results (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    definition_id bigint NOT NULL,
    status text NOT NULL,
    gap_reasons jsonb DEFAULT '{}'::jsonb NOT NULL,
    provisional_build_id bigint,
    history_complete boolean DEFAULT true NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT record_results_status_check CHECK ((status = ANY (ARRAY['resolved'::text, 'unresolved'::text, 'no_candidate'::text])))
);


--
-- Name: TABLE record_results; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_results IS 'The outcome for one definition in a computation run.';


--
-- Name: record_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.record_results ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.record_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: record_rulesets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_rulesets (
    id bigint NOT NULL,
    document_hash text NOT NULL,
    calculator_version text NOT NULL,
    formatter_version text NOT NULL,
    activated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE record_rulesets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_rulesets IS 'An immutable version of the record calculators and title formatters.';


--
-- Name: record_rulesets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.record_rulesets ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.record_rulesets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schematic_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schematic_files (
    sha256 text NOT NULL,
    byte_size integer NOT NULL,
    source_format text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    object_key text NOT NULL,
    CONSTRAINT schematic_files_size_bounded CHECK (((byte_size > 0) AND (byte_size <= 16777216)))
);


--
-- Name: TABLE schematic_files; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.schematic_files IS 'Relational metadata for a content-addressed schematic artifact.';


--
-- Name: COLUMN schematic_files.sha256; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_files.sha256 IS 'Lowercase hex SHA-256 of the object payload, and the identity of this row.';


--
-- Name: COLUMN schematic_files.byte_size; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_files.byte_size IS 'Object payload size used to bound downloads.';


--
-- Name: COLUMN schematic_files.source_format; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_files.source_format IS 'The format the content sniffer identified, e.g. `litematic`.';


--
-- Name: schematic_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schematic_jobs (
    id bigint NOT NULL,
    operation text NOT NULL,
    params jsonb NOT NULL,
    input_keys text[] NOT NULL,
    result jsonb,
    result_object_key text,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    completed_at timestamp with time zone,
    dead_at timestamp with time zone,
    expires_at timestamp with time zone,
    last_error text,
    error_kind text,
    error_context jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT schematic_jobs_operation_check CHECK ((operation = ANY (ARRAY['capabilities'::text, 'analyze'::text, 'convert'::text, 'compare'::text, 'render'::text, 'simulate'::text, 'autostack'::text]))),
    CONSTRAINT schematic_jobs_single_terminal_state CHECK (((completed_at IS NULL) OR (dead_at IS NULL)))
);


--
-- Name: TABLE schematic_jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.schematic_jobs IS 'A durable request for the worker-owned native schematic engine.';


--
-- Name: COLUMN schematic_jobs.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_jobs.claim_token IS 'The database-minted fencing token handed to the worker that claimed this row.';


--
-- Name: schematic_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.schematic_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: schematic_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.schematic_jobs_id_seq OWNED BY public.schematic_jobs.id;


--
-- Name: schematic_preview_objects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schematic_preview_objects (
    object_key text NOT NULL,
    byte_size bigint NOT NULL,
    sha256 text,
    ready_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schematic_preview_objects_sha256_check CHECK (((sha256 IS NULL) OR (sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT schematic_preview_objects_size_positive CHECK ((byte_size > 0))
);


--
-- Name: schematic_render_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schematic_render_queue (
    build_id bigint NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    dead_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid
);


--
-- Name: TABLE schematic_render_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.schematic_render_queue IS 'A durable request to render and publish one build''s primary schematic.';


--
-- Name: COLUMN schematic_render_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_render_queue.available_at IS 'When this row next becomes claimable, and the only column backoff writes.';


--
-- Name: COLUMN schematic_render_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_render_queue.claim_token IS 'The database-minted fencing token handed to the worker that claimed this row.';


--
-- Name: schematic_renders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schematic_renders (
    id bigint NOT NULL,
    build_schematic_id bigint NOT NULL,
    recipe_hash text NOT NULL,
    url text NOT NULL,
    width integer NOT NULL,
    height integer NOT NULL,
    byte_size integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    object_key text,
    CONSTRAINT schematic_renders_sizes_positive CHECK (((width > 0) AND (height > 0) AND (byte_size > 0)))
);


--
-- Name: TABLE schematic_renders; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.schematic_renders IS 'A replaceable preview artifact keyed by the complete rendering recipe.';


--
-- Name: COLUMN schematic_renders.recipe_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_renders.recipe_hash IS 'SHA-256 of the pack, camera recipe, output dimensions, and analyzer version.';


--
-- Name: schematic_renders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.schematic_renders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: schematic_renders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.schematic_renders_id_seq OWNED BY public.schematic_renders.id;


--
-- Name: search_document_facets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_document_facets (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    field_name text NOT NULL,
    ordinal smallint DEFAULT 0 NOT NULL,
    text_value text,
    numeric_value numeric,
    timestamp_value timestamp with time zone,
    boolean_value boolean,
    CONSTRAINT search_document_facets_one_value_check CHECK ((num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1))
);


--
-- Name: TABLE search_document_facets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_document_facets IS 'A typed, indexed field value belonging to a search document.';


--
-- Name: search_document_facets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.search_document_facets ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.search_document_facets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: search_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_documents (
    id bigint NOT NULL,
    resource_kind text NOT NULL,
    source_key text NOT NULL,
    title text NOT NULL,
    subtitle text,
    description text,
    status text,
    normalized_title text NOT NULL,
    fuzzy_text text NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    title_vector tsvector,
    description_vector tsvector,
    combined_vector tsvector,
    document_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_hash text NOT NULL,
    embedding public.vector(1536),
    embedding_model text,
    refreshed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE search_documents; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_documents IS 'An indexed projection of a searchable application resource.';


--
-- Name: search_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.search_documents ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.search_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: search_embedding_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_embedding_queue (
    document_id bigint NOT NULL,
    source_hash text NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    locked_at timestamp with time zone,
    last_error text,
    dead_at timestamp with time zone,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid
);


--
-- Name: TABLE search_embedding_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_embedding_queue IS 'A durable request to embed a search document whose source hash changed.';


--
-- Name: COLUMN search_embedding_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_embedding_queue.available_at IS 'When this row next becomes claimable, and the only column backoff writes.';


--
-- Name: COLUMN search_embedding_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_embedding_queue.claim_token IS 'The database-minted fencing token handed to the worker that claimed this row.';


--
-- Name: search_projection_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_projection_queue (
    id bigint NOT NULL,
    resource_kind text NOT NULL,
    source_key text NOT NULL,
    action text DEFAULT 'upsert'::text NOT NULL,
    enqueued_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    locked_at timestamp with time zone,
    last_error text,
    dead_at timestamp with time zone,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT search_projection_queue_action_check CHECK ((action = ANY (ARRAY['upsert'::text, 'delete'::text])))
);


--
-- Name: TABLE search_projection_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_projection_queue IS 'A durable request to refresh or delete a projected search resource.';


--
-- Name: COLUMN search_projection_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_projection_queue.available_at IS 'When this row next becomes claimable, and the only column backoff writes.';


--
-- Name: COLUMN search_projection_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_projection_queue.claim_token IS 'The database-minted fencing token handed to the worker that claimed this row.';


--
-- Name: search_projection_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.search_projection_queue ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.search_projection_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: server_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.server_settings (
    server_id bigint NOT NULL,
    smallest_channel_id bigint,
    fastest_channel_id bigint,
    first_channel_id bigint,
    builds_channel_id bigint,
    voting_channel_id bigint,
    in_server boolean DEFAULT true NOT NULL,
    locale text
);


--
-- Name: TABLE server_settings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.server_settings IS 'Settings for a Discord server.';


--
-- Name: COLUMN server_settings.locale; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.server_settings.locale IS 'Admin-configured language override, e.g. "en" or "zh-CN". Falls back to Discord''s guild/user locale when unset.';


--
-- Name: starboard_emojis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboard_emojis (
    starboard_id bigint NOT NULL,
    emoji text NOT NULL,
    direction text NOT NULL,
    multiplier double precision DEFAULT 1.0 NOT NULL,
    "position" smallint NOT NULL,
    CONSTRAINT starboard_emojis_direction_check CHECK ((direction = ANY (ARRAY['up'::text, 'down'::text]))),
    CONSTRAINT starboard_emojis_emoji_check CHECK ((btrim(emoji) <> ''::text)),
    CONSTRAINT starboard_emojis_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision))),
    CONSTRAINT starboard_emojis_position_check CHECK (("position" >= 0))
);


--
-- Name: TABLE starboard_emojis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboard_emojis IS 'An ordered upvote or downvote emoji for a starboard.';


--
-- Name: starboard_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboard_entries (
    starboard_id bigint NOT NULL,
    origin_message_id bigint NOT NULL,
    score double precision DEFAULT 0.0 NOT NULL,
    raw_count integer DEFAULT 0 NOT NULL,
    last_rendered_score double precision,
    first_posted_at timestamp with time zone,
    updated_at timestamp with time zone
);


--
-- Name: TABLE starboard_entries; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboard_entries IS 'The materialized-post state for one source message on one starboard.';


--
-- Name: starboard_origin_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboard_origin_messages (
    id bigint NOT NULL,
    guild_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    author_id bigint NOT NULL,
    author_is_bot boolean NOT NULL,
    is_nsfw boolean DEFAULT false NOT NULL,
    has_image boolean DEFAULT false NOT NULL,
    posted_at timestamp with time zone NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: TABLE starboard_origin_messages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboard_origin_messages IS 'A source message that has been evaluated by at least one starboard.';


--
-- Name: starboard_role_multipliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboard_role_multipliers (
    starboard_id bigint NOT NULL,
    role_id bigint NOT NULL,
    multiplier double precision NOT NULL,
    CONSTRAINT starboard_role_multipliers_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision)))
);


--
-- Name: TABLE starboard_role_multipliers; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboard_role_multipliers IS 'A role multiplier scoped to one starboard.';


--
-- Name: starboard_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboard_sources (
    starboard_id bigint NOT NULL,
    guild_id bigint NOT NULL,
    channel_id bigint DEFAULT 0 NOT NULL,
    approved_by bigint,
    approved_at timestamp with time zone
);


--
-- Name: TABLE starboard_sources; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboard_sources IS 'A guild or channel whose messages feed a starboard.';


--
-- Name: starboard_votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboard_votes (
    starboard_id bigint NOT NULL,
    origin_message_id bigint NOT NULL,
    user_id bigint NOT NULL,
    emoji text NOT NULL,
    direction text NOT NULL,
    weight double precision NOT NULL,
    target_author_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT starboard_votes_direction_check CHECK ((direction = ANY (ARRAY['up'::text, 'down'::text]))),
    CONSTRAINT starboard_votes_weight_check CHECK (((weight > (0)::double precision) AND (weight <> 'Infinity'::double precision) AND (weight <> 'NaN'::double precision)))
);


--
-- Name: TABLE starboard_votes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboard_votes IS 'One member''s current weighted reaction to one message on one starboard.';


--
-- Name: starboards; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboards (
    id bigint NOT NULL,
    guild_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    required double precision DEFAULT 3.0 NOT NULL,
    required_remove double precision DEFAULT 0.0 NOT NULL,
    self_vote boolean DEFAULT false NOT NULL,
    allow_bots boolean DEFAULT false NOT NULL,
    require_image boolean DEFAULT false NOT NULL,
    min_age_seconds integer DEFAULT 0 NOT NULL,
    max_age_seconds integer DEFAULT 0 NOT NULL,
    autoreact_upvote boolean DEFAULT true NOT NULL,
    autoreact_downvote boolean DEFAULT true NOT NULL,
    remove_invalid_reactions boolean DEFAULT false NOT NULL,
    link_edits boolean DEFAULT true NOT NULL,
    link_deletes boolean DEFAULT true NOT NULL,
    display_emoji text DEFAULT '⭐'::text NOT NULL,
    colour integer DEFAULT 4415105 NOT NULL,
    jump_to_message boolean DEFAULT true NOT NULL,
    attachments_list boolean DEFAULT true NOT NULL,
    replied_to boolean DEFAULT true NOT NULL,
    ping_author boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT starboards_age_check CHECK (((min_age_seconds >= 0) AND (max_age_seconds >= 0) AND ((max_age_seconds = 0) OR (min_age_seconds <= max_age_seconds)))),
    CONSTRAINT starboards_colour_check CHECK (((colour >= 0) AND (colour <= 16777215))),
    CONSTRAINT starboards_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT starboards_thresholds_check CHECK (((required > required_remove) AND (required <> 'Infinity'::double precision) AND (required <> '-Infinity'::double precision) AND (required <> 'NaN'::double precision) AND (required_remove <> 'Infinity'::double precision) AND (required_remove <> '-Infinity'::double precision) AND (required_remove <> 'NaN'::double precision)))
);


--
-- Name: TABLE starboards; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboards IS 'A named weighted-message board owned by one Discord guild.';


--
-- Name: starboards_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.starboards ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.starboards_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: submission_draft_access; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_draft_access (
    id bigint NOT NULL,
    draft_id uuid NOT NULL,
    account_id integer NOT NULL,
    role text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_draft_access_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'editor'::text])))
);


--
-- Name: TABLE submission_draft_access; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_draft_access IS 'An account''s role on a draft; v1 creates exactly one owner grant.';


--
-- Name: submission_draft_access_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.submission_draft_access ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.submission_draft_access_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: submission_draft_changes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_draft_changes (
    id bigint NOT NULL,
    draft_id uuid NOT NULL,
    actor_account_id integer NOT NULL,
    base_revision integer NOT NULL,
    resulting_revision integer NOT NULL,
    client_instance_id text NOT NULL,
    idempotency_key text NOT NULL,
    operations jsonb NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_draft_changes_base_revision_nonnegative CHECK ((base_revision >= 0)),
    CONSTRAINT submission_draft_changes_has_operations CHECK ((jsonb_array_length(operations) > 0)),
    CONSTRAINT submission_draft_changes_revision_sequence CHECK ((resulting_revision = (base_revision + 1)))
);


--
-- Name: TABLE submission_draft_changes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_draft_changes IS 'An immutable accepted field-operation batch used for retries and audit.';


--
-- Name: submission_draft_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.submission_draft_changes ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.submission_draft_changes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: submission_drafts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_drafts (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    schema_id text NOT NULL,
    schema_revision integer NOT NULL,
    category text NOT NULL,
    revision integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'editing'::text NOT NULL,
    answers jsonb NOT NULL,
    origin text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    source_installation_id uuid,
    CONSTRAINT submission_drafts_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT submission_drafts_installation_requires_paper CHECK (((source_installation_id IS NULL) OR (origin = 'paper'::text))),
    CONSTRAINT submission_drafts_origin_check CHECK ((origin = ANY (ARRAY['discord'::text, 'web'::text, 'cli'::text, 'paper'::text, 'fabric'::text]))),
    CONSTRAINT submission_drafts_revision_nonnegative CHECK ((revision >= 0)),
    CONSTRAINT submission_drafts_schema_revision_positive CHECK ((schema_revision > 0)),
    CONSTRAINT submission_drafts_status_check CHECK ((status = ANY (ARRAY['editing'::text, 'processing'::text, 'needs_attention'::text, 'submitted'::text, 'expired'::text])))
);


--
-- Name: TABLE submission_drafts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_drafts IS 'The compact current state of an account-owned submission draft.';


--
-- Name: COLUMN submission_drafts.source_installation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.submission_drafts.source_installation_id IS 'Server-derived Paper installation retained independently of credential generations.';


--
-- Name: submission_finalization_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_finalization_jobs (
    id uuid NOT NULL,
    draft_id uuid NOT NULL,
    draft_revision integer NOT NULL,
    payload jsonb,
    payload_sha256 text,
    status text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    claim_token uuid,
    claim_expires_at timestamp with time zone,
    completed_at timestamp with time zone,
    attention_at timestamp with time zone,
    dead_at timestamp with time zone,
    last_error text,
    attention_issues jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_finalization_jobs_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT submission_finalization_jobs_legacy_sponsor_forbidden CHECK (((payload IS NULL) OR (NOT (((payload ->> 'payload_schema'::text) = '2'::text) AND ((payload ->> 'sponsor_attribution'::text) = 'true'::text))))),
    CONSTRAINT submission_finalization_jobs_payload_sha256_check CHECK (((payload_sha256 IS NULL) OR (payload_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT submission_finalization_jobs_revision_nonnegative CHECK ((draft_revision >= 0)),
    CONSTRAINT submission_finalization_jobs_state_shape CHECK ((((status = 'pending'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NULL) AND (attention_at IS NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) = 0)) OR ((status = 'claimed'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NOT NULL) AND (claim_token IS NOT NULL) AND (claim_expires_at IS NOT NULL) AND (completed_at IS NULL) AND (attention_at IS NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) = 0)) OR ((status = 'needs_attention'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NULL) AND (attention_at IS NOT NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) > 0)) OR ((status = 'completed'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NOT NULL) AND (attention_at IS NULL) AND (dead_at IS NULL) AND (jsonb_array_length(attention_issues) = 0)) OR ((status = 'dead'::text) AND (payload IS NOT NULL) AND (payload_sha256 IS NOT NULL) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (claim_expires_at IS NULL) AND (completed_at IS NULL) AND (attention_at IS NULL) AND (dead_at IS NOT NULL) AND (jsonb_array_length(attention_issues) > 0)))),
    CONSTRAINT submission_finalization_jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'needs_attention'::text, 'completed'::text, 'dead'::text])))
);


--
-- Name: TABLE submission_finalization_jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_finalization_jobs IS 'A retained source-draft job with UUID-fenced worker claims.';


--
-- Name: submission_finalization_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_finalization_results (
    job_id uuid NOT NULL,
    build_id bigint NOT NULL,
    target_key text,
    provenance jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_finalization_results_build_id_positive CHECK ((build_id > 0)),
    CONSTRAINT submission_finalization_results_target_key_check CHECK (((target_key IS NULL) OR (target_key ~ '^[a-z][a-z0-9_]{0,63}$'::text)))
);


--
-- Name: TABLE submission_finalization_results; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_finalization_results IS 'Immutable build identity and target provenance retained after success.';


--
-- Name: submissions_submission_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.submissions_submission_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: submissions_submission_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.submissions_submission_id_seq OWNED BY public.builds.id;


--
-- Name: tag_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_aliases (
    tag_id bigint NOT NULL,
    alias text NOT NULL,
    normalized_alias text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE tag_aliases; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tag_aliases IS 'An alternate display name for a tag.';


--
-- Name: tag_applicabilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_applicabilities (
    tag_id bigint NOT NULL,
    build_kind text NOT NULL
);


--
-- Name: TABLE tag_applicabilities; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tag_applicabilities IS 'A build kind on which a tag may be used.';


--
-- Name: tag_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_definitions (
    id bigint NOT NULL,
    stable_key text NOT NULL,
    display_name text NOT NULL,
    normalized_name text NOT NULL,
    query_name text,
    authority text NOT NULL,
    semantic_kind text NOT NULL,
    restriction_type text,
    value_type text NOT NULL,
    record_operator text,
    canonical_unit_key text,
    default_display_unit_key text,
    numeric_step numeric,
    render_template text NOT NULL,
    default_display_order smallint NOT NULL,
    moderation_status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    created_by_account_id integer,
    CONSTRAINT tag_definitions_authority_check CHECK ((authority = ANY (ARRAY['official'::text, 'user'::text]))),
    CONSTRAINT tag_definitions_display_order_check CHECK ((default_display_order >= 0)),
    CONSTRAINT tag_definitions_moderation_status_check CHECK ((moderation_status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'archived'::text]))),
    CONSTRAINT tag_definitions_non_numeric_unit_check CHECK (((value_type = 'numeric'::text) OR ((canonical_unit_key IS NULL) AND (default_display_unit_key IS NULL) AND (numeric_step IS NULL)))),
    CONSTRAINT tag_definitions_numeric_step_check CHECK (((numeric_step IS NULL) OR (numeric_step > (0)::numeric))),
    CONSTRAINT tag_definitions_query_name_format_check CHECK (((query_name IS NULL) OR (query_name ~ '^[a-z][a-z0-9_]{0,63}$'::text))),
    CONSTRAINT tag_definitions_record_operator_check CHECK (((record_operator IS NULL) OR (record_operator = ANY (ARRAY['present'::text, 'exact'::text, 'at_most'::text, 'at_least'::text])))),
    CONSTRAINT tag_definitions_record_operator_value_check CHECK ((((record_operator = 'present'::text) AND (value_type = 'none'::text)) OR ((record_operator = ANY (ARRAY['at_most'::text, 'at_least'::text])) AND (value_type = 'numeric'::text)) OR ((record_operator = 'exact'::text) AND (value_type <> 'none'::text)) OR (record_operator IS NULL))),
    CONSTRAINT tag_definitions_restriction_type_check CHECK ((((semantic_kind = 'restriction'::text) AND (restriction_type IS NOT NULL)) OR ((semantic_kind <> 'restriction'::text) AND (restriction_type IS NULL)))),
    CONSTRAINT tag_definitions_semantic_kind_check CHECK ((semantic_kind = ANY (ARRAY['restriction'::text, 'pattern'::text, 'showcase'::text]))),
    CONSTRAINT tag_definitions_user_showcase_only_check CHECK (((authority = 'official'::text) OR ((semantic_kind = 'showcase'::text) AND (restriction_type IS NULL) AND (record_operator IS NULL)))),
    CONSTRAINT tag_definitions_value_type_check CHECK ((value_type = ANY (ARRAY['none'::text, 'numeric'::text, 'text'::text, 'boolean'::text])))
);


--
-- Name: TABLE tag_definitions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tag_definitions IS 'A canonical tag that may be assigned to builds.';


--
-- Name: tag_definitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.tag_definitions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.tag_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: tag_record_thresholds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_record_thresholds (
    tag_id bigint NOT NULL,
    value_type text NOT NULL,
    numeric_value numeric NOT NULL,
    display_order integer NOT NULL,
    CONSTRAINT tag_record_thresholds_numeric_check CHECK ((value_type = 'numeric'::text))
);


--
-- Name: TABLE tag_record_thresholds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tag_record_thresholds IS 'A staff-seeded eager threshold for a parameterized restriction.';


--
-- Name: tag_relations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_relations (
    source_tag_id bigint NOT NULL,
    relation_kind text NOT NULL,
    target_tag_id bigint NOT NULL,
    CONSTRAINT tag_relations_distinct_check CHECK ((source_tag_id <> target_tag_id)),
    CONSTRAINT tag_relations_kind_check CHECK ((relation_kind = ANY (ARRAY['implies'::text, 'incompatible'::text])))
);


--
-- Name: TABLE tag_relations; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tag_relations IS 'A semantic relationship between official restrictions.';


--
-- Name: tag_units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_units (
    key text NOT NULL,
    dimension text NOT NULL,
    symbol text NOT NULL,
    aliases text[] NOT NULL,
    scale_to_base numeric NOT NULL
);


--
-- Name: TABLE tag_units; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tag_units IS 'A unit accepted by numeric tag inputs.';


--
-- Name: utilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.utilities (
    build_id bigint NOT NULL
);


--
-- Name: TABLE utilities; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.utilities IS 'A utility build.';


--
-- Name: verification_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verification_attempts (
    provider text NOT NULL,
    subject text NOT NULL,
    consecutive_failures integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT verification_attempts_failures_non_negative CHECK ((consecutive_failures >= 0)),
    CONSTRAINT verification_attempts_provider_valid CHECK ((provider = ANY (ARRAY['discord'::text, 'java'::text, 'bedrock'::text])))
);


--
-- Name: TABLE verification_attempts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.verification_attempts IS 'Consecutive failed code redemptions for one external identity.

Keyed on `(provider, subject)` rather than on an account, because the guesser may not have an
account yet: a redemption is the first thing many callers ever do, and creating a row for
someone in order to rate-limit them would defeat the point. No foreign key for the same reason.

The counter is *consecutive*: a success clears it, so an honest user who mistypes twice and then
gets it right is never closer to a lockout than someone who never failed.';


--
-- Name: verification_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verification_codes (
    id smallint NOT NULL,
    minecraft_uuid uuid NOT NULL,
    code text NOT NULL,
    created timestamp with time zone DEFAULT now() NOT NULL,
    expires timestamp with time zone DEFAULT (now() + '00:10:00'::interval) NOT NULL,
    username text NOT NULL,
    valid boolean DEFAULT true NOT NULL,
    reserved_token text,
    reserved_until timestamp with time zone,
    CONSTRAINT verification_codes_reservation_complete CHECK (((reserved_token IS NULL) = (reserved_until IS NULL)))
);


--
-- Name: TABLE verification_codes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.verification_codes IS 'A verification code for linking Java Edition identities.';


--
-- Name: COLUMN verification_codes.reserved_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.verification_codes.reserved_token IS 'Digest of the token held by whoever is currently being shown this code''s consent prompt.

A digest rather than the token, for the same reason `code` is one. Deliberately says nothing
about *who* reserved it: the prompt runs before an account exists, and the notice promises that
cancelling stores no account information, so a reservation identifies nobody.';


--
-- Name: COLUMN verification_codes.reserved_until; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.verification_codes.reserved_until IS 'When the hold lapses, freeing the code without anything having to reap it.

A crashed process therefore costs one prompt''s worth of delay rather than a stuck code, and the
legitimate owner can always mint a fresh one from the game.';


--
-- Name: verification_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verification_codes_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verification_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verification_codes_id_seq OWNED BY public.verification_codes.id;


--
-- Name: versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.versions (
    id smallint NOT NULL,
    edition text NOT NULL,
    major_version smallint NOT NULL,
    minor_version smallint NOT NULL,
    patch_number smallint NOT NULL,
    data_version integer
);


--
-- Name: TABLE versions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.versions IS 'A version of Minecraft that a build is compatible with.';


--
-- Name: COLUMN versions.data_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.versions.data_version IS 'Minecraft''s own numeric world-format version, needed to retarget a schematic at this
release. Nullable because Bedrock has no equivalent and Java releases predating the field
have none either.';


--
-- Name: versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.versions_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.versions_id_seq OWNED BY public.versions.id;


--
-- Name: vote_session_options; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vote_session_options (
    vote_session_id bigint NOT NULL,
    emoji text NOT NULL,
    choice text NOT NULL,
    multiplier double precision DEFAULT 1.0 NOT NULL,
    "position" smallint NOT NULL,
    identifier text NOT NULL,
    guild_id bigint DEFAULT 0 NOT NULL,
    label text,
    CONSTRAINT vote_session_options_choice_check CHECK ((choice = ANY (ARRAY['approve'::text, 'deny'::text, 'generic'::text]))),
    CONSTRAINT vote_session_options_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision))),
    CONSTRAINT vote_session_options_position_check CHECK (("position" >= 0))
);


--
-- Name: TABLE vote_session_options; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.vote_session_options IS 'Ordered reaction options and positive weight multipliers captured for each vote session.';


--
-- Name: vote_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vote_sessions (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    status text NOT NULL,
    kind text NOT NULL,
    fail_threshold smallint,
    pass_threshold smallint,
    result text DEFAULT 'pending'::text NOT NULL,
    author_account_id integer NOT NULL,
    CONSTRAINT vote_sessions_kind_check CHECK ((kind = ANY (ARRAY['build'::text, 'delete_log'::text, 'generic'::text]))),
    CONSTRAINT vote_sessions_result_check CHECK ((result = ANY (ARRAY['approved'::text, 'denied'::text, 'cancelled'::text, 'pending'::text]))),
    CONSTRAINT vote_sessions_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text]))),
    CONSTRAINT vote_sessions_threshold_kind_check CHECK (
CASE
    WHEN (kind = 'generic'::text) THEN ((pass_threshold IS NULL) AND (fail_threshold IS NULL))
    ELSE ((pass_threshold IS NOT NULL) AND (fail_threshold IS NOT NULL) AND (pass_threshold > 0) AND (fail_threshold < 0))
END)
);


--
-- Name: TABLE vote_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.vote_sessions IS 'A voting session for builds, log deletions, or generic polls.';


--
-- Name: COLUMN vote_sessions.result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vote_sessions.result IS 'The result of the vote session.';


--
-- Name: vote_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.vote_sessions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.vote_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.votes (
    vote_session_id bigint NOT NULL,
    weight double precision NOT NULL,
    guild_id bigint DEFAULT 0 NOT NULL,
    option_id text NOT NULL,
    emoji text NOT NULL,
    account_id integer NOT NULL,
    CONSTRAINT votes_weight_check CHECK (((weight > (0)::double precision) AND (weight <> 'Infinity'::double precision) AND (weight <> 'NaN'::double precision)))
);


--
-- Name: TABLE votes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.votes IS 'A vote cast in a vote session.';


--
-- Name: web_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.web_sessions (
    id uuid NOT NULL,
    token_hash bytea NOT NULL,
    account_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    user_agent text
);


--
-- Name: TABLE web_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.web_sessions IS 'A revocable opaque browser session.';


--
-- Name: accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts ALTER COLUMN id SET DEFAULT nextval('public.accounts_id_seq'::regclass);


--
-- Name: build_schematics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_schematics ALTER COLUMN id SET DEFAULT nextval('public.build_schematics_id_seq'::regclass);


--
-- Name: builds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.builds ALTER COLUMN id SET DEFAULT nextval('public.submissions_submission_id_seq'::regclass);


--
-- Name: creator_alias_claims id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_alias_claims ALTER COLUMN id SET DEFAULT nextval('public.creator_alias_claims_id_seq'::regclass);


--
-- Name: creator_aliases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_aliases ALTER COLUMN id SET DEFAULT nextval('public.creator_aliases_id_seq'::regclass);


--
-- Name: schematic_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_jobs ALTER COLUMN id SET DEFAULT nextval('public.schematic_jobs_id_seq'::regclass);


--
-- Name: schematic_renders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_renders ALTER COLUMN id SET DEFAULT nextval('public.schematic_renders_id_seq'::regclass);


--
-- Name: verification_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verification_codes ALTER COLUMN id SET DEFAULT nextval('public.verification_codes_id_seq'::regclass);


--
-- Name: versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.versions ALTER COLUMN id SET DEFAULT nextval('public.versions_id_seq'::regclass);


--
-- Name: account_identities account_identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_identities
    ADD CONSTRAINT account_identities_pkey PRIMARY KEY (id);


--
-- Name: account_identities account_identities_provider_subject_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_identities
    ADD CONSTRAINT account_identities_provider_subject_key UNIQUE (provider, subject);


--
-- Name: account_merge_tickets account_merge_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_merge_tickets
    ADD CONSTRAINT account_merge_tickets_pkey PRIMARY KEY (account_id);


--
-- Name: account_profiles account_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_profiles
    ADD CONSTRAINT account_profiles_pkey PRIMARY KEY (account_id);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);


--
-- Name: accounts accounts_public_creator_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_public_creator_id_key UNIQUE (public_creator_id);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: build_creators build_creators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_pkey PRIMARY KEY (build_id, alias_id);


--
-- Name: build_links build_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_links
    ADD CONSTRAINT build_links_pkey PRIMARY KEY (build_id, url);


--
-- Name: build_schematics build_schematics_build_file_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_build_file_key UNIQUE (build_id, file_sha256);


--
-- Name: build_schematics build_schematics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_pkey PRIMARY KEY (id);


--
-- Name: build_source_messages build_source_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_source_messages
    ADD CONSTRAINT build_source_messages_pkey PRIMARY KEY (build_id, message_id);


--
-- Name: build_tag_assignments build_tag_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_pkey PRIMARY KEY (build_id, tag_id);


--
-- Name: build_versions build_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_versions
    ADD CONSTRAINT build_versions_pkey PRIMARY KEY (build_id, version_id);


--
-- Name: build_vote_sessions build_vote_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_vote_sessions
    ADD CONSTRAINT build_vote_sessions_pkey PRIMARY KEY (vote_session_id);


--
-- Name: builds builds_source_submission_draft_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.builds
    ADD CONSTRAINT builds_source_submission_draft_id_key UNIQUE (source_submission_draft_id);


--
-- Name: cli_device_enrollments cli_device_enrollments_device_code_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_device_code_hash_key UNIQUE (device_code_hash);


--
-- Name: cli_device_enrollments cli_device_enrollments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_pkey PRIMARY KEY (id);


--
-- Name: cli_device_enrollments cli_device_enrollments_user_code_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_user_code_hash_key UNIQUE (user_code_hash);


--
-- Name: cli_devices cli_devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_devices
    ADD CONSTRAINT cli_devices_pkey PRIMARY KEY (id);


--
-- Name: cli_devices cli_devices_public_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_devices
    ADD CONSTRAINT cli_devices_public_key_key UNIQUE (public_key);


--
-- Name: cli_session_challenges cli_session_challenges_nonce_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_session_challenges
    ADD CONSTRAINT cli_session_challenges_nonce_hash_key UNIQUE (nonce_hash);


--
-- Name: cli_session_challenges cli_session_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_session_challenges
    ADD CONSTRAINT cli_session_challenges_pkey PRIMARY KEY (id);


--
-- Name: cli_sessions cli_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_sessions
    ADD CONSTRAINT cli_sessions_pkey PRIMARY KEY (id);


--
-- Name: cli_sessions cli_sessions_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_sessions
    ADD CONSTRAINT cli_sessions_token_hash_key UNIQUE (token_hash);


--
-- Name: creator_alias_claims creator_alias_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_pkey PRIMARY KEY (id);


--
-- Name: creator_aliases creator_aliases_normalized_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_aliases
    ADD CONSTRAINT creator_aliases_normalized_name_key UNIQUE (normalized_name);


--
-- Name: creator_aliases creator_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_aliases
    ADD CONSTRAINT creator_aliases_pkey PRIMARY KEY (id);


--
-- Name: delete_log_vote_sessions delete_log_vote_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.delete_log_vote_sessions
    ADD CONSTRAINT delete_log_vote_sessions_pkey PRIMARY KEY (vote_session_id);


--
-- Name: discord_posts discord_posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discord_posts
    ADD CONSTRAINT discord_posts_pkey PRIMARY KEY (message_id);


--
-- Name: discord_sync_queue discord_sync_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discord_sync_queue
    ADD CONSTRAINT discord_sync_queue_pkey PRIMARY KEY (id);


--
-- Name: discord_sync_queue discord_sync_queue_resource_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discord_sync_queue
    ADD CONSTRAINT discord_sync_queue_resource_key UNIQUE (resource_kind, source_key);


--
-- Name: domain_event_consumers domain_event_consumers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domain_event_consumers
    ADD CONSTRAINT domain_event_consumers_pkey PRIMARY KEY (name);


--
-- Name: domain_event_deliveries domain_event_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domain_event_deliveries
    ADD CONSTRAINT domain_event_deliveries_pkey PRIMARY KEY (event_id, consumer);


--
-- Name: domain_events domain_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domain_events
    ADD CONSTRAINT domain_events_pkey PRIMARY KEY (id);


--
-- Name: door_timing_variants door_timing_variants_build_label_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.door_timing_variants
    ADD CONSTRAINT door_timing_variants_build_label_key UNIQUE (build_id, label);


--
-- Name: door_timing_variants door_timing_variants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.door_timing_variants
    ADD CONSTRAINT door_timing_variants_pkey PRIMARY KEY (id);


--
-- Name: doors doors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doors
    ADD CONSTRAINT doors_pkey PRIMARY KEY (build_id);


--
-- Name: entrances entrances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entrances
    ADD CONSTRAINT entrances_pkey PRIMARY KEY (build_id);


--
-- Name: error_reports error_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.error_reports
    ADD CONSTRAINT error_reports_pkey PRIMARY KEY (id);


--
-- Name: extender_timing_variants extender_timing_variants_build_label_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extender_timing_variants
    ADD CONSTRAINT extender_timing_variants_build_label_key UNIQUE (build_id, label);


--
-- Name: extender_timing_variants extender_timing_variants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extender_timing_variants
    ADD CONSTRAINT extender_timing_variants_pkey PRIMARY KEY (id);


--
-- Name: extenders extenders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extenders
    ADD CONSTRAINT extenders_pkey PRIMARY KEY (build_id);


--
-- Name: generic_vote_sessions generic_vote_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generic_vote_sessions
    ADD CONSTRAINT generic_vote_sessions_pkey PRIMARY KEY (vote_session_id);


--
-- Name: guild_vote_emojis guild_vote_emojis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_vote_emojis
    ADD CONSTRAINT guild_vote_emojis_pkey PRIMARY KEY (guild_id, kind, emoji);


--
-- Name: guild_vote_emojis guild_vote_emojis_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_vote_emojis
    ADD CONSTRAINT guild_vote_emojis_position_key UNIQUE (guild_id, kind, "position");


--
-- Name: guild_vote_role_weights guild_vote_role_weights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_vote_role_weights
    ADD CONSTRAINT guild_vote_role_weights_pkey PRIMARY KEY (guild_id, kind, role_id);


--
-- Name: idempotency_requests idempotency_requests_caller_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_requests
    ADD CONSTRAINT idempotency_requests_caller_key UNIQUE (caller, idempotency_key);


--
-- Name: idempotency_requests idempotency_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_requests
    ADD CONSTRAINT idempotency_requests_pkey PRIMARY KEY (id);


--
-- Name: idempotency_requests idempotency_requests_principal_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.idempotency_requests
    ADD CONSTRAINT idempotency_requests_principal_key UNIQUE (principal, idempotency_key);


--
-- Name: media_artifact_objects media_artifact_objects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifact_objects
    ADD CONSTRAINT media_artifact_objects_pkey PRIMARY KEY (object_key);


--
-- Name: media_artifact_publications media_artifact_publications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifact_publications
    ADD CONSTRAINT media_artifact_publications_pkey PRIMARY KEY (object_key, upload_id, claim_token);


--
-- Name: media_artifacts media_artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_pkey PRIMARY KEY (id);


--
-- Name: media_artifacts media_artifacts_upload_role_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_upload_role_key UNIQUE (upload_id, role);


--
-- Name: media_normalization_jobs media_normalization_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_normalization_jobs
    ADD CONSTRAINT media_normalization_jobs_pkey PRIMARY KEY (upload_id);


--
-- Name: media_uploads media_uploads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_uploads
    ADD CONSTRAINT media_uploads_pkey PRIMARY KEY (id);


--
-- Name: media_uploads media_uploads_source_object_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_uploads
    ADD CONSTRAINT media_uploads_source_object_key_key UNIQUE (source_object_key);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: minecraft_paper_installations minecraft_paper_installations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_paper_installations
    ADD CONSTRAINT minecraft_paper_installations_pkey PRIMARY KEY (id);


--
-- Name: minecraft_player_challenges minecraft_player_challenges_device_code_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_device_code_hash_key UNIQUE (device_code_hash);


--
-- Name: minecraft_player_challenges minecraft_player_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_pkey PRIMARY KEY (id);


--
-- Name: minecraft_player_challenges minecraft_player_challenges_user_code_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_user_code_hash_key UNIQUE (user_code_hash);


--
-- Name: minecraft_player_grants minecraft_player_grants_challenge_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_challenge_id_key UNIQUE (challenge_id);


--
-- Name: minecraft_player_grants minecraft_player_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_pkey PRIMARY KEY (id);


--
-- Name: minecraft_player_grants minecraft_player_grants_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_token_hash_key UNIQUE (token_hash);


--
-- Name: notification_deliveries notification_deliveries_notification_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_notification_id_key UNIQUE (notification_id);


--
-- Name: notification_deliveries notification_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_pkey PRIMARY KEY (id);


--
-- Name: notification_profiles notification_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_profiles
    ADD CONSTRAINT notification_profiles_pkey PRIMARY KEY (account_id);


--
-- Name: notification_subscriptions notification_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_subscriptions
    ADD CONSTRAINT notification_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_id_account_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_id_account_key UNIQUE (id, account_id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_source_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_source_key_key UNIQUE (source_key);


--
-- Name: oauth_states oauth_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_states
    ADD CONSTRAINT oauth_states_pkey PRIMARY KEY (state);


--
-- Name: other_builds other_builds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.other_builds
    ADD CONSTRAINT other_builds_pkey PRIMARY KEY (build_id);


--
-- Name: permission_audit_log permission_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_audit_log
    ADD CONSTRAINT permission_audit_log_pkey PRIMARY KEY (id);


--
-- Name: permission_epoch permission_epoch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_epoch
    ADD CONSTRAINT permission_epoch_pkey PRIMARY KEY (id);


--
-- Name: permission_grants permission_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_grants
    ADD CONSTRAINT permission_grants_pkey PRIMARY KEY (id);


--
-- Name: permission_role_assignments permission_role_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_pkey PRIMARY KEY (id);


--
-- Name: permission_role_includes permission_role_includes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_pkey PRIMARY KEY (role_id, included_role_id);


--
-- Name: permission_role_patterns permission_role_patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_patterns
    ADD CONSTRAINT permission_role_patterns_pkey PRIMARY KEY (role_id, pattern);


--
-- Name: permission_roles permission_roles_builtin_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_builtin_key_key UNIQUE (builtin_key);


--
-- Name: permission_roles permission_roles_guild_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_guild_slug_key UNIQUE NULLS NOT DISTINCT (guild_id, slug);


--
-- Name: permission_roles permission_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_pkey PRIMARY KEY (id);


--
-- Name: public_creator_redirects public_creator_redirects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.public_creator_redirects
    ADD CONSTRAINT public_creator_redirects_pkey PRIMARY KEY (retired_public_creator_id);


--
-- Name: record_competitions record_competitions_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_competitions
    ADD CONSTRAINT record_competitions_identity_key UNIQUE NULLS NOT DISTINCT (record_class, build_kind, version_scope, version_id, category_key);


--
-- Name: record_competitions record_competitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_competitions
    ADD CONSTRAINT record_competitions_pkey PRIMARY KEY (public_id);


--
-- Name: record_computation_runs record_computation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_pkey PRIMARY KEY (id);


--
-- Name: record_definition_facets record_definition_facets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_definition_facets
    ADD CONSTRAINT record_definition_facets_pkey PRIMARY KEY (definition_id, facet_kind, facet_id);


--
-- Name: record_definitions record_definitions_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_definitions
    ADD CONSTRAINT record_definitions_identity_key UNIQUE NULLS NOT DISTINCT (ruleset_id, record_class, build_kind, version_scope, version_id, category_key);


--
-- Name: record_definitions record_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_definitions
    ADD CONSTRAINT record_definitions_pkey PRIMARY KEY (id);


--
-- Name: record_holder_history record_holder_history_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_holder_history
    ADD CONSTRAINT record_holder_history_identity_key UNIQUE (run_id, definition_id, build_id, held_from);


--
-- Name: record_holder_history record_holder_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_holder_history
    ADD CONSTRAINT record_holder_history_pkey PRIMARY KEY (id);


--
-- Name: record_recompute_queue record_recompute_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_recompute_queue
    ADD CONSTRAINT record_recompute_queue_pkey PRIMARY KEY (id);


--
-- Name: record_recompute_queue record_recompute_queue_scope_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_recompute_queue
    ADD CONSTRAINT record_recompute_queue_scope_key_key UNIQUE (scope_key);


--
-- Name: record_result_holders record_result_holders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_result_holders
    ADD CONSTRAINT record_result_holders_pkey PRIMARY KEY (result_id, build_id);


--
-- Name: record_results record_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_results
    ADD CONSTRAINT record_results_pkey PRIMARY KEY (id);


--
-- Name: record_results record_results_run_definition_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_results
    ADD CONSTRAINT record_results_run_definition_key UNIQUE (run_id, definition_id);


--
-- Name: record_rulesets record_rulesets_content_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_rulesets
    ADD CONSTRAINT record_rulesets_content_key UNIQUE (document_hash, calculator_version, formatter_version);


--
-- Name: record_rulesets record_rulesets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_rulesets
    ADD CONSTRAINT record_rulesets_pkey PRIMARY KEY (id);


--
-- Name: schematic_files schematic_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_files
    ADD CONSTRAINT schematic_files_pkey PRIMARY KEY (sha256);


--
-- Name: schematic_jobs schematic_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_jobs
    ADD CONSTRAINT schematic_jobs_pkey PRIMARY KEY (id);


--
-- Name: schematic_preview_objects schematic_preview_objects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_preview_objects
    ADD CONSTRAINT schematic_preview_objects_pkey PRIMARY KEY (object_key);


--
-- Name: schematic_render_queue schematic_render_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_render_queue
    ADD CONSTRAINT schematic_render_queue_pkey PRIMARY KEY (build_id);


--
-- Name: schematic_renders schematic_renders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_pkey PRIMARY KEY (id);


--
-- Name: schematic_renders schematic_renders_schematic_recipe_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_schematic_recipe_key UNIQUE (build_schematic_id, recipe_hash);


--
-- Name: search_document_facets search_document_facets_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_document_facets
    ADD CONSTRAINT search_document_facets_identity_key UNIQUE (document_id, field_name, ordinal);


--
-- Name: search_document_facets search_document_facets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_document_facets
    ADD CONSTRAINT search_document_facets_pkey PRIMARY KEY (id);


--
-- Name: search_documents search_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_documents
    ADD CONSTRAINT search_documents_pkey PRIMARY KEY (id);


--
-- Name: search_documents search_documents_resource_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_documents
    ADD CONSTRAINT search_documents_resource_key UNIQUE (resource_kind, source_key);


--
-- Name: search_embedding_queue search_embedding_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_embedding_queue
    ADD CONSTRAINT search_embedding_queue_pkey PRIMARY KEY (document_id);


--
-- Name: search_projection_queue search_projection_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_projection_queue
    ADD CONSTRAINT search_projection_queue_pkey PRIMARY KEY (id);


--
-- Name: search_projection_queue search_projection_queue_resource_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_projection_queue
    ADD CONSTRAINT search_projection_queue_resource_key UNIQUE (resource_kind, source_key);


--
-- Name: server_settings server_settings_fastest_channel_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_fastest_channel_id_key UNIQUE (fastest_channel_id);


--
-- Name: server_settings server_settings_first_channel_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_first_channel_id_key UNIQUE (first_channel_id);


--
-- Name: server_settings server_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_pkey PRIMARY KEY (server_id);


--
-- Name: server_settings server_settings_smallest_channel_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.server_settings
    ADD CONSTRAINT server_settings_smallest_channel_id_key UNIQUE (smallest_channel_id);


--
-- Name: starboard_emojis starboard_emojis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_emojis
    ADD CONSTRAINT starboard_emojis_pkey PRIMARY KEY (starboard_id, emoji);


--
-- Name: starboard_entries starboard_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_entries
    ADD CONSTRAINT starboard_entries_pkey PRIMARY KEY (starboard_id, origin_message_id);


--
-- Name: starboard_origin_messages starboard_origin_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_origin_messages
    ADD CONSTRAINT starboard_origin_messages_pkey PRIMARY KEY (id);


--
-- Name: starboard_role_multipliers starboard_role_multipliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_role_multipliers
    ADD CONSTRAINT starboard_role_multipliers_pkey PRIMARY KEY (starboard_id, role_id);


--
-- Name: starboard_sources starboard_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_sources
    ADD CONSTRAINT starboard_sources_pkey PRIMARY KEY (starboard_id, guild_id, channel_id);


--
-- Name: starboard_votes starboard_votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_votes
    ADD CONSTRAINT starboard_votes_pkey PRIMARY KEY (starboard_id, origin_message_id, user_id);


--
-- Name: starboards starboards_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboards
    ADD CONSTRAINT starboards_pkey PRIMARY KEY (id);


--
-- Name: submission_draft_access submission_draft_access_draft_account_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_draft_account_key UNIQUE (draft_id, account_id);


--
-- Name: submission_draft_access submission_draft_access_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_pkey PRIMARY KEY (id);


--
-- Name: submission_draft_changes submission_draft_changes_draft_idempotency_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_draft_idempotency_key UNIQUE (draft_id, idempotency_key);


--
-- Name: submission_draft_changes submission_draft_changes_draft_revision_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_draft_revision_key UNIQUE (draft_id, resulting_revision);


--
-- Name: submission_draft_changes submission_draft_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_pkey PRIMARY KEY (id);


--
-- Name: submission_drafts submission_drafts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_pkey PRIMARY KEY (id);


--
-- Name: submission_finalization_jobs submission_finalization_jobs_draft_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_draft_id_key UNIQUE (draft_id);


--
-- Name: submission_finalization_jobs submission_finalization_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_pkey PRIMARY KEY (id);


--
-- Name: submission_finalization_results submission_finalization_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_results
    ADD CONSTRAINT submission_finalization_results_pkey PRIMARY KEY (job_id);


--
-- Name: builds submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.builds
    ADD CONSTRAINT submissions_pkey PRIMARY KEY (id);


--
-- Name: tag_aliases tag_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_aliases
    ADD CONSTRAINT tag_aliases_pkey PRIMARY KEY (tag_id, normalized_alias);


--
-- Name: tag_applicabilities tag_applicabilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_applicabilities
    ADD CONSTRAINT tag_applicabilities_pkey PRIMARY KEY (tag_id, build_kind);


--
-- Name: tag_definitions tag_definitions_id_value_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_id_value_type_key UNIQUE (id, value_type);


--
-- Name: tag_definitions tag_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_pkey PRIMARY KEY (id);


--
-- Name: tag_definitions tag_definitions_query_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_query_name_key UNIQUE (query_name);


--
-- Name: tag_definitions tag_definitions_stable_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_stable_key_key UNIQUE (stable_key);


--
-- Name: tag_record_thresholds tag_record_thresholds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_record_thresholds
    ADD CONSTRAINT tag_record_thresholds_pkey PRIMARY KEY (tag_id, numeric_value);


--
-- Name: tag_relations tag_relations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_relations
    ADD CONSTRAINT tag_relations_pkey PRIMARY KEY (source_tag_id, relation_kind, target_tag_id);


--
-- Name: tag_units tag_units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_units
    ADD CONSTRAINT tag_units_pkey PRIMARY KEY (key);


--
-- Name: utilities utilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utilities
    ADD CONSTRAINT utilities_pkey PRIMARY KEY (build_id);


--
-- Name: verification_attempts verification_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verification_attempts
    ADD CONSTRAINT verification_attempts_pkey PRIMARY KEY (provider, subject);


--
-- Name: verification_codes verification_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verification_codes
    ADD CONSTRAINT verification_codes_pkey PRIMARY KEY (id);


--
-- Name: versions versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_pkey PRIMARY KEY (id);


--
-- Name: vote_session_options vote_session_options_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_pkey PRIMARY KEY (vote_session_id, guild_id, emoji);


--
-- Name: vote_session_options vote_session_options_vote_session_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_vote_session_id_position_key UNIQUE (vote_session_id, guild_id, "position");


--
-- Name: vote_sessions vote_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_sessions
    ADD CONSTRAINT vote_sessions_pkey PRIMARY KEY (id);


--
-- Name: votes votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_pkey PRIMARY KEY (vote_session_id, account_id);


--
-- Name: web_sessions web_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_pkey PRIMARY KEY (id);


--
-- Name: web_sessions web_sessions_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_token_hash_key UNIQUE (token_hash);


--
-- Name: account_identities_account_provider_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX account_identities_account_provider_idx ON public.account_identities USING btree (account_id, provider);


--
-- Name: account_merge_tickets_code_digest_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX account_merge_tickets_code_digest_idx ON public.account_merge_tickets USING btree (code_digest);


--
-- Name: account_profiles_avatar_identity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX account_profiles_avatar_identity_idx ON public.account_profiles USING btree (avatar_identity_id);


--
-- Name: api_keys_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX api_keys_active ON public.api_keys USING btree (key_id) WHERE (revoked_at IS NULL);


--
-- Name: api_keys_created_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX api_keys_created_by_idx ON public.api_keys USING btree (created_by_account_id);


--
-- Name: api_keys_key_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX api_keys_key_id_key ON public.api_keys USING btree (key_id);


--
-- Name: api_keys_owner_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX api_keys_owner_idx ON public.api_keys USING btree (owner_account_id);


--
-- Name: build_creators_alias_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_creators_alias_idx ON public.build_creators USING btree (alias_id);


--
-- Name: build_schematics_block_count_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_block_count_idx ON public.build_schematics USING btree (block_count);


--
-- Name: build_schematics_build_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_build_id_idx ON public.build_schematics USING btree (build_id);


--
-- Name: build_schematics_file_sha256_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_file_sha256_idx ON public.build_schematics USING btree (file_sha256);


--
-- Name: build_schematics_fingerprint_shape_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_fingerprint_shape_idx ON public.build_schematics USING btree (fingerprint_shape, analyzer_version) WHERE (fingerprint_shape IS NOT NULL);


--
-- Name: build_schematics_fingerprint_structural_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_fingerprint_structural_idx ON public.build_schematics USING btree (fingerprint_structural, analyzer_version) WHERE (fingerprint_structural IS NOT NULL);


--
-- Name: build_schematics_one_primary_per_build; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX build_schematics_one_primary_per_build ON public.build_schematics USING btree (build_id) WHERE is_primary;


--
-- Name: build_schematics_public_download_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_public_download_idx ON public.build_schematics USING btree (build_id, id) WHERE ((visibility = 'public_download'::text) AND (withdrawn_at IS NULL));


--
-- Name: build_schematics_rights_attested_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_rights_attested_by_idx ON public.build_schematics USING btree (rights_attested_by_account_id);


--
-- Name: build_schematics_uploaded_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_schematics_uploaded_by_idx ON public.build_schematics USING btree (uploaded_by_account_id);


--
-- Name: build_tag_assignments_created_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_tag_assignments_created_by_idx ON public.build_tag_assignments USING btree (created_by_account_id);


--
-- Name: build_tag_assignments_numeric_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_tag_assignments_numeric_idx ON public.build_tag_assignments USING btree (tag_id, numeric_value, build_id) WHERE (numeric_value IS NOT NULL);


--
-- Name: build_tag_assignments_tag_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_tag_assignments_tag_build_idx ON public.build_tag_assignments USING btree (tag_id, build_id);


--
-- Name: build_tag_assignments_text_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_tag_assignments_text_idx ON public.build_tag_assignments USING btree (tag_id, text_value, build_id) WHERE (text_value IS NOT NULL);


--
-- Name: build_versions_version_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_versions_version_idx ON public.build_versions USING btree (version_id);


--
-- Name: build_vote_sessions_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_vote_sessions_build_idx ON public.build_vote_sessions USING btree (build_id);


--
-- Name: builds_embedding_hnsw_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX builds_embedding_hnsw_idx ON public.builds USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);


--
-- Name: builds_submitter_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX builds_submitter_idx ON public.builds USING btree (submitter_account_id);


--
-- Name: cli_device_enrollments_active_client_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cli_device_enrollments_active_client_idx ON public.cli_device_enrollments USING btree (client_instance_id, expires_at) WHERE ((exchanged_at IS NULL) AND (revoked_at IS NULL));


--
-- Name: cli_device_enrollments_approved_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cli_device_enrollments_approved_by_idx ON public.cli_device_enrollments USING btree (approved_by_account_id);


--
-- Name: cli_devices_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cli_devices_account_idx ON public.cli_devices USING btree (account_id, created_at);


--
-- Name: cli_devices_active_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cli_devices_active_account_idx ON public.cli_devices USING btree (account_id, last_used_at) WHERE (revoked_at IS NULL);


--
-- Name: cli_session_challenges_active_device_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cli_session_challenges_active_device_idx ON public.cli_session_challenges USING btree (device_id, expires_at) WHERE (consumed_at IS NULL);


--
-- Name: cli_sessions_active_device_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cli_sessions_active_device_idx ON public.cli_sessions USING btree (device_id, expires_at) WHERE (revoked_at IS NULL);


--
-- Name: cli_sessions_device_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cli_sessions_device_idx ON public.cli_sessions USING btree (device_id, expires_at);


--
-- Name: creator_alias_claims_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX creator_alias_claims_account_idx ON public.creator_alias_claims USING btree (account_id);


--
-- Name: creator_alias_claims_one_pending_per_account; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX creator_alias_claims_one_pending_per_account ON public.creator_alias_claims USING btree (alias_id, account_id) WHERE (status = 'pending'::text);


--
-- Name: creator_alias_claims_resolved_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX creator_alias_claims_resolved_by_idx ON public.creator_alias_claims USING btree (resolved_by_account_id);


--
-- Name: creator_aliases_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX creator_aliases_account_idx ON public.creator_aliases USING btree (account_id);


--
-- Name: creator_aliases_normalized_name_prefix_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX creator_aliases_normalized_name_prefix_idx ON public.creator_aliases USING btree (normalized_name text_pattern_ops);


--
-- Name: discord_posts_resource_channel_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX discord_posts_resource_channel_key ON public.discord_posts USING btree (resource_kind, resource_key, channel_id) WHERE (suppressed_at IS NULL);


--
-- Name: discord_posts_resource_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX discord_posts_resource_idx ON public.discord_posts USING btree (resource_kind, resource_key);


--
-- Name: discord_sync_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX discord_sync_queue_ready_idx ON public.discord_sync_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));


--
-- Name: domain_event_deliveries_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX domain_event_deliveries_ready_idx ON public.domain_event_deliveries USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));


--
-- Name: domain_events_aggregate_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX domain_events_aggregate_idx ON public.domain_events USING btree (aggregate_kind, aggregate_id);


--
-- Name: error_reports_correlation_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX error_reports_correlation_id_idx ON public.error_reports USING btree (correlation_id);


--
-- Name: error_reports_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX error_reports_expires_at_idx ON public.error_reports USING btree (expires_at);


--
-- Name: error_reports_occurred_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX error_reports_occurred_at_idx ON public.error_reports USING btree (occurred_at);


--
-- Name: error_reports_reference_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX error_reports_reference_idx ON public.error_reports USING btree (reference);


--
-- Name: error_reports_work_lost_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX error_reports_work_lost_idx ON public.error_reports USING btree (occurred_at) WHERE work_lost;


--
-- Name: generic_vote_sessions_deadline_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX generic_vote_sessions_deadline_idx ON public.generic_vote_sessions USING btree (deadline);


--
-- Name: generic_vote_sessions_guild_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX generic_vote_sessions_guild_idx ON public.generic_vote_sessions USING btree (guild_id);


--
-- Name: idempotency_requests_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idempotency_requests_expires_at_idx ON public.idempotency_requests USING btree (expires_at);


--
-- Name: idx_builds_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_builds_category ON public.builds USING btree (category) WHERE (category IS NOT NULL);


--
-- Name: idx_builds_record_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_builds_record_category ON public.builds USING btree (record_category) WHERE (record_category IS NOT NULL);


--
-- Name: idx_builds_submission_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_builds_submission_time ON public.builds USING btree (submission_time DESC);


--
-- Name: media_artifact_objects_cleanup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_artifact_objects_cleanup_idx ON public.media_artifact_objects USING btree (available_at) WHERE (deleted_at IS NULL);


--
-- Name: media_artifact_publications_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_artifact_publications_expiry_idx ON public.media_artifact_publications USING btree (expires_at);


--
-- Name: media_artifacts_object_key_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_artifacts_object_key_idx ON public.media_artifacts USING btree (object_key);


--
-- Name: media_artifacts_sha256_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_artifacts_sha256_idx ON public.media_artifacts USING btree (sha256);


--
-- Name: media_normalization_jobs_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_normalization_jobs_ready_idx ON public.media_normalization_jobs USING btree (available_at) WHERE (status = ANY (ARRAY['pending'::text, 'claimed'::text]));


--
-- Name: media_normalization_jobs_terminal_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_normalization_jobs_terminal_idx ON public.media_normalization_jobs USING btree (upload_id) WHERE (status = ANY (ARRAY['completed'::text, 'dead'::text, 'discarded'::text]));


--
-- Name: media_uploads_draft_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_uploads_draft_idx ON public.media_uploads USING btree (draft_id, created_at);


--
-- Name: media_uploads_raw_cleanup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_uploads_raw_cleanup_idx ON public.media_uploads USING btree (created_at) WHERE (raw_deleted_at IS NULL);


--
-- Name: messages_guild_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX messages_guild_idx ON public.messages USING btree (guild_id);


--
-- Name: minecraft_paper_installations_owner_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX minecraft_paper_installations_owner_idx ON public.minecraft_paper_installations USING btree (owner_account_id, created_at);


--
-- Name: minecraft_paper_installations_public_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX minecraft_paper_installations_public_idx ON public.minecraft_paper_installations USING btree (created_at) WHERE (public_profile_enabled AND (revoked_at IS NULL));


--
-- Name: minecraft_player_challenges_active_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX minecraft_player_challenges_active_lookup_idx ON public.minecraft_player_challenges USING btree (origin, java_uuid, installation_id, expires_at) WHERE ((exchanged_at IS NULL) AND (revoked_at IS NULL));


--
-- Name: minecraft_player_challenges_approved_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX minecraft_player_challenges_approved_by_idx ON public.minecraft_player_challenges USING btree (approved_by_account_id);


--
-- Name: minecraft_player_grants_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX minecraft_player_grants_account_idx ON public.minecraft_player_grants USING btree (account_id, expires_at);


--
-- Name: minecraft_player_grants_active_installation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX minecraft_player_grants_active_installation_idx ON public.minecraft_player_grants USING btree (installation_id, expires_at) WHERE (revoked_at IS NULL);


--
-- Name: notification_deliveries_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notification_deliveries_account_idx ON public.notification_deliveries USING btree (account_id);


--
-- Name: notification_deliveries_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notification_deliveries_ready_idx ON public.notification_deliveries USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL) AND (sent_at IS NULL));


--
-- Name: notification_subscriptions_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notification_subscriptions_account_idx ON public.notification_subscriptions USING btree (account_id, created_at);


--
-- Name: notification_subscriptions_exact_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX notification_subscriptions_exact_key ON public.notification_subscriptions USING btree (account_id, kind, subject_id) WHERE (enabled AND (subject_id IS NOT NULL));


--
-- Name: notification_subscriptions_filter_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX notification_subscriptions_filter_key ON public.notification_subscriptions USING btree (account_id, kind, filter) WHERE (enabled AND (filter IS NOT NULL));


--
-- Name: notification_subscriptions_subject_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notification_subscriptions_subject_idx ON public.notification_subscriptions USING btree (kind, subject_id);


--
-- Name: notifications_account_inbox_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifications_account_inbox_idx ON public.notifications USING btree (account_id, id);


--
-- Name: notifications_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifications_created_idx ON public.notifications USING btree (created_at);


--
-- Name: permission_audit_log_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_audit_log_actor_idx ON public.permission_audit_log USING btree (actor_account_id);


--
-- Name: permission_audit_log_by_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_audit_log_by_subject ON public.permission_audit_log USING btree (subject_kind, subject_id);


--
-- Name: permission_audit_log_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_audit_log_recent ON public.permission_audit_log USING btree (at);


--
-- Name: permission_grants_account_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX permission_grants_account_unique ON public.permission_grants USING btree (subject_account_id, pattern, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_account_id IS NOT NULL);


--
-- Name: permission_grants_by_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_grants_by_account ON public.permission_grants USING btree (subject_account_id) WHERE (subject_account_id IS NOT NULL);


--
-- Name: permission_grants_by_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_grants_by_role ON public.permission_grants USING btree (subject_guild_id, subject_role_id) WHERE (subject_role_id IS NOT NULL);


--
-- Name: permission_grants_granted_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_grants_granted_by_idx ON public.permission_grants USING btree (granted_by_account_id);


--
-- Name: permission_grants_role_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX permission_grants_role_unique ON public.permission_grants USING btree (subject_role_id, pattern, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_role_id IS NOT NULL);


--
-- Name: permission_role_assignments_account_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX permission_role_assignments_account_unique ON public.permission_role_assignments USING btree (subject_account_id, role_id, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_account_id IS NOT NULL);


--
-- Name: permission_role_assignments_by_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_role_assignments_by_account ON public.permission_role_assignments USING btree (subject_account_id) WHERE (subject_account_id IS NOT NULL);


--
-- Name: permission_role_assignments_by_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_role_assignments_by_role ON public.permission_role_assignments USING btree (subject_guild_id, subject_role_id) WHERE (subject_role_id IS NOT NULL);


--
-- Name: permission_role_assignments_granted_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_role_assignments_granted_by_idx ON public.permission_role_assignments USING btree (granted_by_account_id);


--
-- Name: permission_role_assignments_role_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_role_assignments_role_idx ON public.permission_role_assignments USING btree (role_id);


--
-- Name: permission_role_assignments_role_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX permission_role_assignments_role_unique ON public.permission_role_assignments USING btree (subject_role_id, role_id, scope_guild_id) NULLS NOT DISTINCT WHERE (subject_role_id IS NOT NULL);


--
-- Name: permission_role_includes_added_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_role_includes_added_by_idx ON public.permission_role_includes USING btree (added_by_account_id);


--
-- Name: permission_role_includes_included_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_role_includes_included_idx ON public.permission_role_includes USING btree (included_role_id);


--
-- Name: permission_role_patterns_added_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_role_patterns_added_by_idx ON public.permission_role_patterns USING btree (added_by_account_id);


--
-- Name: permission_roles_created_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX permission_roles_created_by_idx ON public.permission_roles USING btree (created_by_account_id);


--
-- Name: public_creator_redirects_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX public_creator_redirects_target_idx ON public.public_creator_redirects USING btree (target_account_id);


--
-- Name: record_computation_runs_one_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX record_computation_runs_one_active_idx ON public.record_computation_runs USING btree (build_kind, version_id) NULLS NOT DISTINCT WHERE is_active;


--
-- Name: record_computation_runs_started_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_computation_runs_started_idx ON public.record_computation_runs USING btree (started_at);


--
-- Name: record_definition_facets_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_definition_facets_lookup_idx ON public.record_definition_facets USING btree (facet_kind, facet_id);


--
-- Name: record_definitions_category_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_definitions_category_idx ON public.record_definitions USING btree (build_kind, record_class, category_key);


--
-- Name: record_holder_history_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_holder_history_build_idx ON public.record_holder_history USING btree (build_id);


--
-- Name: record_holder_history_definition_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_holder_history_definition_idx ON public.record_holder_history USING btree (definition_id, held_from);


--
-- Name: record_recompute_queue_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_recompute_queue_build_idx ON public.record_recompute_queue USING btree (build_id);


--
-- Name: record_recompute_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_recompute_queue_ready_idx ON public.record_recompute_queue USING btree (available_at) WHERE (locked_at IS NULL);


--
-- Name: record_result_holders_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_result_holders_build_idx ON public.record_result_holders USING btree (build_id);


--
-- Name: record_results_definition_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_results_definition_idx ON public.record_results USING btree (definition_id);


--
-- Name: record_results_provisional_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_results_provisional_build_idx ON public.record_results USING btree (provisional_build_id);


--
-- Name: schematic_jobs_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schematic_jobs_expiry_idx ON public.schematic_jobs USING btree (expires_at) WHERE (expires_at IS NOT NULL);


--
-- Name: schematic_jobs_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schematic_jobs_ready_idx ON public.schematic_jobs USING btree (available_at) WHERE ((completed_at IS NULL) AND (dead_at IS NULL));


--
-- Name: schematic_preview_objects_cleanup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schematic_preview_objects_cleanup_idx ON public.schematic_preview_objects USING btree (last_seen_at);


--
-- Name: schematic_render_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX schematic_render_queue_ready_idx ON public.schematic_render_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));


--
-- Name: search_document_facets_boolean_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_document_facets_boolean_idx ON public.search_document_facets USING btree (field_name, boolean_value) WHERE (boolean_value IS NOT NULL);


--
-- Name: search_document_facets_numeric_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_document_facets_numeric_idx ON public.search_document_facets USING btree (field_name, numeric_value) WHERE (numeric_value IS NOT NULL);


--
-- Name: search_document_facets_text_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_document_facets_text_idx ON public.search_document_facets USING btree (field_name, text_value) WHERE (text_value IS NOT NULL);


--
-- Name: search_document_facets_text_prefix_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_document_facets_text_prefix_idx ON public.search_document_facets USING btree (field_name, lower(text_value) text_pattern_ops) WHERE (text_value IS NOT NULL);


--
-- Name: search_document_facets_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_document_facets_timestamp_idx ON public.search_document_facets USING btree (field_name, timestamp_value) WHERE (timestamp_value IS NOT NULL);


--
-- Name: search_documents_combined_fts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_documents_combined_fts_idx ON public.search_documents USING gin (combined_vector);


--
-- Name: search_documents_description_fts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_documents_description_fts_idx ON public.search_documents USING gin (description_vector);


--
-- Name: search_documents_embedding_hnsw_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_documents_embedding_hnsw_idx ON public.search_documents USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);


--
-- Name: search_documents_fuzzy_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_documents_fuzzy_trgm_idx ON public.search_documents USING gin (fuzzy_text public.gin_trgm_ops);


--
-- Name: search_documents_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_documents_scope_idx ON public.search_documents USING btree (resource_kind, status);


--
-- Name: search_documents_tags_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_documents_tags_idx ON public.search_documents USING gin (tags);


--
-- Name: search_documents_title_fts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_documents_title_fts_idx ON public.search_documents USING gin (title_vector);


--
-- Name: search_embedding_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_embedding_queue_ready_idx ON public.search_embedding_queue USING btree (available_at) WHERE ((locked_at IS NULL) AND (dead_at IS NULL));


--
-- Name: search_projection_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_projection_queue_ready_idx ON public.search_projection_queue USING btree (available_at) WHERE ((locked_at IS NULL) AND (dead_at IS NULL));


--
-- Name: starboard_emojis_position_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX starboard_emojis_position_key ON public.starboard_emojis USING btree (starboard_id, "position");


--
-- Name: starboard_entries_origin_message_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX starboard_entries_origin_message_idx ON public.starboard_entries USING btree (origin_message_id);


--
-- Name: starboard_entries_score_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX starboard_entries_score_idx ON public.starboard_entries USING btree (starboard_id, score DESC);


--
-- Name: starboard_origin_messages_guild_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX starboard_origin_messages_guild_idx ON public.starboard_origin_messages USING btree (guild_id);


--
-- Name: starboard_sources_guild_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX starboard_sources_guild_idx ON public.starboard_sources USING btree (guild_id);


--
-- Name: starboard_votes_origin_message_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX starboard_votes_origin_message_idx ON public.starboard_votes USING btree (origin_message_id);


--
-- Name: starboard_votes_target_author_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX starboard_votes_target_author_created_idx ON public.starboard_votes USING btree (starboard_id, target_author_id, created_at);


--
-- Name: starboards_guild_name_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX starboards_guild_name_key ON public.starboards USING btree (guild_id, lower(name));


--
-- Name: submission_draft_access_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_draft_access_account_idx ON public.submission_draft_access USING btree (account_id, draft_id);


--
-- Name: submission_draft_access_one_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX submission_draft_access_one_owner ON public.submission_draft_access USING btree (draft_id) WHERE (role = 'owner'::text);


--
-- Name: submission_draft_changes_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_draft_changes_actor_idx ON public.submission_draft_changes USING btree (actor_account_id, applied_at);


--
-- Name: submission_drafts_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_expiry_idx ON public.submission_drafts USING btree (expires_at) WHERE (status = ANY (ARRAY['editing'::text, 'processing'::text, 'needs_attention'::text]));


--
-- Name: submission_drafts_owner_updated_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_owner_updated_idx ON public.submission_drafts USING btree (owner_account_id, updated_at);


--
-- Name: submission_drafts_source_installation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_source_installation_idx ON public.submission_drafts USING btree (source_installation_id) WHERE (source_installation_id IS NOT NULL);


--
-- Name: submission_finalization_jobs_attention_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_finalization_jobs_attention_idx ON public.submission_finalization_jobs USING btree (attention_at) WHERE (status = 'needs_attention'::text);


--
-- Name: submission_finalization_jobs_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_finalization_jobs_ready_idx ON public.submission_finalization_jobs USING btree (available_at) WHERE (status = ANY (ARRAY['pending'::text, 'claimed'::text]));


--
-- Name: submission_finalization_results_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_finalization_results_build_idx ON public.submission_finalization_results USING btree (build_id);


--
-- Name: tag_aliases_normalized_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tag_aliases_normalized_idx ON public.tag_aliases USING btree (normalized_alias);


--
-- Name: tag_definitions_created_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tag_definitions_created_by_idx ON public.tag_definitions USING btree (created_by_account_id);


--
-- Name: tag_definitions_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tag_definitions_lookup_idx ON public.tag_definitions USING btree (normalized_name, semantic_kind);


--
-- Name: tag_relations_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tag_relations_target_idx ON public.tag_relations USING btree (target_tag_id);


--
-- Name: vote_sessions_author_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vote_sessions_author_idx ON public.vote_sessions USING btree (author_account_id);


--
-- Name: votes_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX votes_account_idx ON public.votes USING btree (account_id);


--
-- Name: web_sessions_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX web_sessions_account_idx ON public.web_sessions USING btree (account_id);


--
-- Name: web_sessions_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX web_sessions_active_idx ON public.web_sessions USING btree (expires_at) WHERE (revoked_at IS NULL);


--
-- Name: build_creators build_creators_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_creators_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_creators build_creators_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_creators_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_links build_links_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_links_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_tag_assignments build_tag_assignments_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_tag_assignments_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_tag_assignments build_tag_assignments_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_tag_assignments_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_versions build_versions_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_versions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_versions build_versions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_vote_sessions build_vote_sessions_kind_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER build_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.build_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();


--
-- Name: builds builds_emit_domain_event; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_emit_domain_event AFTER INSERT OR UPDATE OF submission_status ON public.builds FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();


--
-- Name: builds builds_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: builds builds_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: creator_aliases creator_aliases_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER creator_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.creator_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: delete_log_vote_sessions delete_log_vote_sessions_kind_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER delete_log_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.delete_log_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();


--
-- Name: builds delete_orphaned_build_vote_sessions_after_builds; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds AFTER DELETE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete();


--
-- Name: door_timing_variants door_timing_variants_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER door_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: door_timing_variants door_timing_variants_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER door_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: doors doors_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: doors doors_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: extender_timing_variants extender_timing_variants_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extender_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: extender_timing_variants extender_timing_variants_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extender_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: extenders extenders_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extenders_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: extenders extenders_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extenders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: generic_vote_sessions generic_vote_sessions_kind_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER generic_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.generic_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();


--
-- Name: idempotency_requests idempotency_requests_mirror_caller; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER idempotency_requests_mirror_caller BEFORE INSERT OR UPDATE OF caller, principal ON public.idempotency_requests FOR EACH ROW EXECUTE FUNCTION public.idempotency_requests_mirror_caller();


--
-- Name: permission_grants permission_grants_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_grants_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_grants FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_role_assignments permission_role_assignments_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_role_assignments_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_role_assignments FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_role_includes permission_role_includes_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_role_includes_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_role_includes FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_role_patterns permission_role_patterns_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_role_patterns_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_role_patterns FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_roles permission_roles_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_roles_bump_epoch AFTER INSERT OR DELETE OR UPDATE ON public.permission_roles FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: record_computation_runs record_computation_runs_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER record_computation_runs_enqueue_search AFTER INSERT OR DELETE OR UPDATE OF is_active ON public.record_computation_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();


--
-- Name: record_result_holders record_result_holders_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER record_result_holders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.record_result_holders FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();


--
-- Name: record_results record_results_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER record_results_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.record_results FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();


--
-- Name: builds set_locked_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_locked_at BEFORE UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.set_locked_at();


--
-- Name: starboard_entries starboard_entries_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER starboard_entries_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE OF score ON public.starboard_entries FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();


--
-- Name: starboard_origin_messages starboard_origin_messages_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER starboard_origin_messages_enqueue_discord_sync AFTER UPDATE OF deleted_at ON public.starboard_origin_messages FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();


--
-- Name: starboards starboards_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER starboards_enqueue_discord_sync AFTER UPDATE ON public.starboards FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();


--
-- Name: tag_aliases tag_aliases_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER tag_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: tag_definitions tag_definitions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER tag_definitions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_definitions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: versions versions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: vote_sessions vote_sessions_emit_domain_event; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vote_sessions_emit_domain_event AFTER UPDATE OF status ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();


--
-- Name: vote_sessions vote_sessions_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vote_sessions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: vote_sessions vote_sessions_kind_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();


--
-- Name: votes votes_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER votes_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.votes FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: account_identities account_identities_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_identities
    ADD CONSTRAINT account_identities_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: account_merge_tickets account_merge_tickets_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_merge_tickets
    ADD CONSTRAINT account_merge_tickets_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: account_profiles account_profiles_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_profiles
    ADD CONSTRAINT account_profiles_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: account_profiles account_profiles_avatar_identity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_profiles
    ADD CONSTRAINT account_profiles_avatar_identity_id_fkey FOREIGN KEY (avatar_identity_id) REFERENCES public.account_identities(id) ON DELETE SET NULL;


--
-- Name: api_keys api_keys_created_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: api_keys api_keys_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: build_creators build_creators_alias_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_alias_id_fkey FOREIGN KEY (alias_id) REFERENCES public.creator_aliases(id) ON DELETE RESTRICT;


--
-- Name: build_creators build_creators_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_links build_links_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_links
    ADD CONSTRAINT build_links_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_schematics build_schematics_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_schematics build_schematics_file_sha256_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_file_sha256_fkey FOREIGN KEY (file_sha256) REFERENCES public.schematic_files(sha256) ON DELETE RESTRICT;


--
-- Name: build_schematics build_schematics_rights_attested_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_rights_attested_by_account_id_fkey FOREIGN KEY (rights_attested_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: build_schematics build_schematics_uploaded_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_schematics
    ADD CONSTRAINT build_schematics_uploaded_by_account_id_fkey FOREIGN KEY (uploaded_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: build_source_messages build_source_messages_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_source_messages
    ADD CONSTRAINT build_source_messages_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_source_messages build_source_messages_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_source_messages
    ADD CONSTRAINT build_source_messages_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE RESTRICT;


--
-- Name: build_tag_assignments build_tag_assignments_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_tag_assignments build_tag_assignments_created_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: build_tag_assignments build_tag_assignments_definition_value_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_definition_value_fkey FOREIGN KEY (tag_id, value_type) REFERENCES public.tag_definitions(id, value_type) ON DELETE RESTRICT;


--
-- Name: build_tag_assignments build_tag_assignments_display_unit_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_display_unit_fkey FOREIGN KEY (display_unit_key) REFERENCES public.tag_units(key) ON DELETE RESTRICT;


--
-- Name: build_versions build_versions_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_versions
    ADD CONSTRAINT build_versions_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_versions build_versions_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_versions
    ADD CONSTRAINT build_versions_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;


--
-- Name: build_vote_sessions build_vote_sessions_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_vote_sessions
    ADD CONSTRAINT build_vote_sessions_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: build_vote_sessions build_vote_sessions_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_vote_sessions
    ADD CONSTRAINT build_vote_sessions_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: builds builds_submitter_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.builds
    ADD CONSTRAINT builds_submitter_account_id_fkey FOREIGN KEY (submitter_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: cli_device_enrollments cli_device_enrollments_approved_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_device_enrollments
    ADD CONSTRAINT cli_device_enrollments_approved_account_id_fkey FOREIGN KEY (approved_by_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: cli_devices cli_devices_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_devices
    ADD CONSTRAINT cli_devices_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: cli_session_challenges cli_session_challenges_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_session_challenges
    ADD CONSTRAINT cli_session_challenges_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.cli_devices(id) ON DELETE CASCADE;


--
-- Name: cli_sessions cli_sessions_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cli_sessions
    ADD CONSTRAINT cli_sessions_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.cli_devices(id) ON DELETE CASCADE;


--
-- Name: creator_alias_claims creator_alias_claims_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: creator_alias_claims creator_alias_claims_alias_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_alias_id_fkey FOREIGN KEY (alias_id) REFERENCES public.creator_aliases(id) ON DELETE CASCADE;


--
-- Name: creator_alias_claims creator_alias_claims_resolved_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_alias_claims
    ADD CONSTRAINT creator_alias_claims_resolved_by_account_id_fkey FOREIGN KEY (resolved_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: creator_aliases creator_aliases_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.creator_aliases
    ADD CONSTRAINT creator_aliases_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: delete_log_vote_sessions delete_log_vote_sessions_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.delete_log_vote_sessions
    ADD CONSTRAINT delete_log_vote_sessions_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: discord_posts discord_posts_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.discord_posts
    ADD CONSTRAINT discord_posts_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE RESTRICT;


--
-- Name: domain_event_deliveries domain_event_deliveries_consumer_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domain_event_deliveries
    ADD CONSTRAINT domain_event_deliveries_consumer_fkey FOREIGN KEY (consumer) REFERENCES public.domain_event_consumers(name) ON DELETE CASCADE;


--
-- Name: domain_event_deliveries domain_event_deliveries_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domain_event_deliveries
    ADD CONSTRAINT domain_event_deliveries_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.domain_events(id) ON DELETE CASCADE;


--
-- Name: door_timing_variants door_timing_variants_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.door_timing_variants
    ADD CONSTRAINT door_timing_variants_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.doors(build_id) ON DELETE CASCADE;


--
-- Name: doors doors_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doors
    ADD CONSTRAINT doors_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: entrances entrances_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entrances
    ADD CONSTRAINT entrances_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: extender_timing_variants extender_timing_variants_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extender_timing_variants
    ADD CONSTRAINT extender_timing_variants_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.extenders(build_id) ON DELETE CASCADE;


--
-- Name: extenders extenders_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.extenders
    ADD CONSTRAINT extenders_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: generic_vote_sessions generic_vote_sessions_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generic_vote_sessions
    ADD CONSTRAINT generic_vote_sessions_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE RESTRICT;


--
-- Name: generic_vote_sessions generic_vote_sessions_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generic_vote_sessions
    ADD CONSTRAINT generic_vote_sessions_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: guild_vote_emojis guild_vote_emojis_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_vote_emojis
    ADD CONSTRAINT guild_vote_emojis_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;


--
-- Name: guild_vote_role_weights guild_vote_role_weights_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.guild_vote_role_weights
    ADD CONSTRAINT guild_vote_role_weights_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;


--
-- Name: media_artifact_publications media_artifact_publications_object_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifact_publications
    ADD CONSTRAINT media_artifact_publications_object_key_fkey FOREIGN KEY (object_key) REFERENCES public.media_artifact_objects(object_key) ON DELETE CASCADE;


--
-- Name: media_artifacts media_artifacts_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE CASCADE;


--
-- Name: media_normalization_jobs media_normalization_jobs_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_normalization_jobs
    ADD CONSTRAINT media_normalization_jobs_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE CASCADE;


--
-- Name: minecraft_paper_installations minecraft_paper_installations_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_paper_installations
    ADD CONSTRAINT minecraft_paper_installations_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: minecraft_player_challenges minecraft_player_challenges_approved_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_approved_account_id_fkey FOREIGN KEY (approved_by_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: minecraft_player_challenges minecraft_player_challenges_installation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_challenges
    ADD CONSTRAINT minecraft_player_challenges_installation_id_fkey FOREIGN KEY (installation_id) REFERENCES public.minecraft_paper_installations(id) ON DELETE CASCADE;


--
-- Name: minecraft_player_grants minecraft_player_grants_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: minecraft_player_grants minecraft_player_grants_challenge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_challenge_id_fkey FOREIGN KEY (challenge_id) REFERENCES public.minecraft_player_challenges(id) ON DELETE CASCADE;


--
-- Name: minecraft_player_grants minecraft_player_grants_installation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.minecraft_player_grants
    ADD CONSTRAINT minecraft_player_grants_installation_id_fkey FOREIGN KEY (installation_id) REFERENCES public.minecraft_paper_installations(id) ON DELETE CASCADE;


--
-- Name: notification_deliveries notification_deliveries_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: notification_deliveries notification_deliveries_notification_owner_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT notification_deliveries_notification_owner_fkey FOREIGN KEY (notification_id, account_id) REFERENCES public.notifications(id, account_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;


--
-- Name: notification_profiles notification_profiles_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_profiles
    ADD CONSTRAINT notification_profiles_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: notification_subscriptions notification_subscriptions_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_subscriptions
    ADD CONSTRAINT notification_subscriptions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.domain_events(id) ON DELETE CASCADE;


--
-- Name: other_builds other_builds_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.other_builds
    ADD CONSTRAINT other_builds_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: permission_audit_log permission_audit_log_actor_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_audit_log
    ADD CONSTRAINT permission_audit_log_actor_fkey FOREIGN KEY (actor_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: permission_grants permission_grants_granted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_grants
    ADD CONSTRAINT permission_grants_granted_by_fkey FOREIGN KEY (granted_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: permission_grants permission_grants_subject_account_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_grants
    ADD CONSTRAINT permission_grants_subject_account_fkey FOREIGN KEY (subject_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: permission_role_assignments permission_role_assignments_granted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_granted_by_fkey FOREIGN KEY (granted_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: permission_role_assignments permission_role_assignments_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;


--
-- Name: permission_role_assignments permission_role_assignments_subject_account_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_assignments
    ADD CONSTRAINT permission_role_assignments_subject_account_fkey FOREIGN KEY (subject_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: permission_role_includes permission_role_includes_added_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_added_by_fkey FOREIGN KEY (added_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: permission_role_includes permission_role_includes_included_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_included_role_id_fkey FOREIGN KEY (included_role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;


--
-- Name: permission_role_includes permission_role_includes_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_includes
    ADD CONSTRAINT permission_role_includes_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;


--
-- Name: permission_role_patterns permission_role_patterns_added_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_patterns
    ADD CONSTRAINT permission_role_patterns_added_by_fkey FOREIGN KEY (added_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: permission_role_patterns permission_role_patterns_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_role_patterns
    ADD CONSTRAINT permission_role_patterns_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.permission_roles(id) ON DELETE CASCADE;


--
-- Name: permission_roles permission_roles_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permission_roles
    ADD CONSTRAINT permission_roles_created_by_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: public_creator_redirects public_creator_redirects_target_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.public_creator_redirects
    ADD CONSTRAINT public_creator_redirects_target_account_id_fkey FOREIGN KEY (target_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: messages public_messages_server_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT public_messages_server_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE RESTRICT;


--
-- Name: record_competitions record_competitions_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_competitions
    ADD CONSTRAINT record_competitions_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;


--
-- Name: record_computation_runs record_computation_runs_ruleset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_ruleset_id_fkey FOREIGN KEY (ruleset_id) REFERENCES public.record_rulesets(id) ON DELETE RESTRICT;


--
-- Name: record_computation_runs record_computation_runs_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;


--
-- Name: record_definition_facets record_definition_facets_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_definition_facets
    ADD CONSTRAINT record_definition_facets_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.record_definitions(id) ON DELETE CASCADE;


--
-- Name: record_definitions record_definitions_competition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_definitions
    ADD CONSTRAINT record_definitions_competition_id_fkey FOREIGN KEY (competition_id) REFERENCES public.record_competitions(public_id) ON DELETE RESTRICT;


--
-- Name: record_definitions record_definitions_ruleset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_definitions
    ADD CONSTRAINT record_definitions_ruleset_id_fkey FOREIGN KEY (ruleset_id) REFERENCES public.record_rulesets(id) ON DELETE RESTRICT;


--
-- Name: record_definitions record_definitions_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_definitions
    ADD CONSTRAINT record_definitions_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;


--
-- Name: record_holder_history record_holder_history_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_holder_history
    ADD CONSTRAINT record_holder_history_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;


--
-- Name: record_holder_history record_holder_history_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_holder_history
    ADD CONSTRAINT record_holder_history_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.record_definitions(id) ON DELETE RESTRICT;


--
-- Name: record_holder_history record_holder_history_predecessor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_holder_history
    ADD CONSTRAINT record_holder_history_predecessor_id_fkey FOREIGN KEY (predecessor_id) REFERENCES public.record_holder_history(id) ON DELETE SET NULL;


--
-- Name: record_holder_history record_holder_history_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_holder_history
    ADD CONSTRAINT record_holder_history_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.record_computation_runs(id) ON DELETE CASCADE;


--
-- Name: record_recompute_queue record_recompute_queue_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_recompute_queue
    ADD CONSTRAINT record_recompute_queue_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: record_result_holders record_result_holders_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_result_holders
    ADD CONSTRAINT record_result_holders_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;


--
-- Name: record_result_holders record_result_holders_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_result_holders
    ADD CONSTRAINT record_result_holders_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.record_results(id) ON DELETE CASCADE;


--
-- Name: record_results record_results_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_results
    ADD CONSTRAINT record_results_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.record_definitions(id) ON DELETE RESTRICT;


--
-- Name: record_results record_results_provisional_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_results
    ADD CONSTRAINT record_results_provisional_build_id_fkey FOREIGN KEY (provisional_build_id) REFERENCES public.builds(id) ON DELETE SET NULL;


--
-- Name: record_results record_results_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_results
    ADD CONSTRAINT record_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.record_computation_runs(id) ON DELETE CASCADE;


--
-- Name: schematic_render_queue schematic_render_queue_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_render_queue
    ADD CONSTRAINT schematic_render_queue_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: schematic_renders schematic_renders_build_schematic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_build_schematic_id_fkey FOREIGN KEY (build_schematic_id) REFERENCES public.build_schematics(id) ON DELETE CASCADE;


--
-- Name: schematic_renders schematic_renders_object_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schematic_renders
    ADD CONSTRAINT schematic_renders_object_key_fkey FOREIGN KEY (object_key) REFERENCES public.schematic_preview_objects(object_key) ON DELETE RESTRICT;


--
-- Name: search_document_facets search_document_facets_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_document_facets
    ADD CONSTRAINT search_document_facets_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.search_documents(id) ON DELETE CASCADE;


--
-- Name: search_embedding_queue search_embedding_queue_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_embedding_queue
    ADD CONSTRAINT search_embedding_queue_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.search_documents(id) ON DELETE CASCADE;


--
-- Name: starboard_emojis starboard_emojis_starboard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_emojis
    ADD CONSTRAINT starboard_emojis_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


--
-- Name: starboard_entries starboard_entries_origin_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_entries
    ADD CONSTRAINT starboard_entries_origin_message_id_fkey FOREIGN KEY (origin_message_id) REFERENCES public.starboard_origin_messages(id) ON DELETE CASCADE;


--
-- Name: starboard_entries starboard_entries_starboard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_entries
    ADD CONSTRAINT starboard_entries_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


--
-- Name: starboard_origin_messages starboard_origin_messages_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_origin_messages
    ADD CONSTRAINT starboard_origin_messages_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;


--
-- Name: starboard_role_multipliers starboard_role_multipliers_starboard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_role_multipliers
    ADD CONSTRAINT starboard_role_multipliers_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


--
-- Name: starboard_sources starboard_sources_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_sources
    ADD CONSTRAINT starboard_sources_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;


--
-- Name: starboard_sources starboard_sources_starboard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_sources
    ADD CONSTRAINT starboard_sources_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


--
-- Name: starboard_votes starboard_votes_origin_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_votes
    ADD CONSTRAINT starboard_votes_origin_message_id_fkey FOREIGN KEY (origin_message_id) REFERENCES public.starboard_origin_messages(id) ON DELETE CASCADE;


--
-- Name: starboard_votes starboard_votes_starboard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_votes
    ADD CONSTRAINT starboard_votes_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


--
-- Name: starboards starboards_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboards
    ADD CONSTRAINT starboards_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;


--
-- Name: submission_draft_access submission_draft_access_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: submission_draft_access submission_draft_access_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_access
    ADD CONSTRAINT submission_draft_access_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;


--
-- Name: submission_draft_changes submission_draft_changes_actor_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_actor_account_id_fkey FOREIGN KEY (actor_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_draft_changes submission_draft_changes_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_changes
    ADD CONSTRAINT submission_draft_changes_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;


--
-- Name: submission_drafts submission_drafts_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: submission_finalization_jobs submission_finalization_jobs_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;


--
-- Name: submission_finalization_results submission_finalization_results_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_results
    ADD CONSTRAINT submission_finalization_results_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.submission_finalization_jobs(id) ON DELETE CASCADE;


--
-- Name: tag_aliases tag_aliases_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_aliases
    ADD CONSTRAINT tag_aliases_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;


--
-- Name: tag_applicabilities tag_applicabilities_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_applicabilities
    ADD CONSTRAINT tag_applicabilities_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;


--
-- Name: tag_definitions tag_definitions_canonical_unit_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_canonical_unit_fkey FOREIGN KEY (canonical_unit_key) REFERENCES public.tag_units(key) ON DELETE RESTRICT;


--
-- Name: tag_definitions tag_definitions_created_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- Name: tag_definitions tag_definitions_display_unit_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_display_unit_fkey FOREIGN KEY (default_display_unit_key) REFERENCES public.tag_units(key) ON DELETE RESTRICT;


--
-- Name: tag_record_thresholds tag_record_thresholds_definition_value_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_record_thresholds
    ADD CONSTRAINT tag_record_thresholds_definition_value_fkey FOREIGN KEY (tag_id, value_type) REFERENCES public.tag_definitions(id, value_type) ON DELETE CASCADE;


--
-- Name: tag_relations tag_relations_source_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_relations
    ADD CONSTRAINT tag_relations_source_fkey FOREIGN KEY (source_tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;


--
-- Name: tag_relations tag_relations_target_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_relations
    ADD CONSTRAINT tag_relations_target_fkey FOREIGN KEY (target_tag_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;


--
-- Name: utilities utilities_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utilities
    ADD CONSTRAINT utilities_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: vote_session_options vote_session_options_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: vote_sessions vote_sessions_author_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_sessions
    ADD CONSTRAINT vote_sessions_author_account_id_fkey FOREIGN KEY (author_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: votes votes_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: votes votes_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: web_sessions web_sessions_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.web_sessions
    ADD CONSTRAINT web_sessions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

