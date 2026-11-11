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
-- Name: begin_build_edit(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: build_history_snapshot_v1(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb
    LANGUAGE sql STABLE
    SET "TimeZone" TO 'UTC'
    AS $$
SELECT jsonb_build_object(
    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),
    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),
    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type) FROM public.extenders d WHERE d.build_id = b.id),
    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', a.account_id) ORDER BY a.id) FROM public.build_creators c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),
    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),
    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value::text, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'provenance', r.provenance, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),
    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),
    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),
    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)
) FROM public.builds b WHERE b.id = target_build_id;
$$;


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
-- Name: capture_build_edit(bigint); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: capture_build_edit_trigger(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: check_permission_role_include_cycle(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: close_build_edit(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.close_build_edit() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM set_config('squid.build_edit_open', 'false', true);
END;
$$;


--
-- Name: delete_vote_session_after_build_vote_session_delete(); Type: FUNCTION; Schema: public; Owner: -
--

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
-- Name: enforce_build_category_subtype(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: enqueue_computed_record_search_projection(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: enqueue_submission_status(); Type: FUNCTION; Schema: public; Owner: -
--

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


--
-- Name: finish_build_edit_trigger(); Type: FUNCTION; Schema: public; Owner: -
--

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
        -- Child-only and direct SQL writes must invalidate aggregate editors too.
        -- ORM saves already advance the root, so do not advance it a second time.
        IF final_snapshot IS NOT NULL AND final_revision <= pending.before_revision THEN
            UPDATE public.builds SET revision = pending.before_revision + 1
            WHERE id = pending.build_id RETURNING revision INTO final_revision;
        END IF;
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
-- Name: reject_build_history_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_build_history_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'build history is append-only' USING ERRCODE = '55000';
END;
$$;


--
-- Name: reject_submission_finalization_input_update(); Type: FUNCTION; Schema: public; Owner: -
--

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

COMMENT ON COLUMN public.account_identities.avatar_key IS 'Provider avatar key, such as a Discord avatar hash. Java avatars use the subject UUID.';


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

COMMENT ON TABLE public.account_merge_tickets IS 'A single-use proof that an account consents to being merged into another account.';


--
-- Name: COLUMN account_merge_tickets.code_digest; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_merge_tickets.code_digest IS 'Digest of the code shown once when the ticket is minted.';


--
-- Name: COLUMN account_merge_tickets.expires_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_merge_tickets.expires_at IS 'End of the authentication proof window in which the ticket can be redeemed.';


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

COMMENT ON TABLE public.account_profiles IS 'Public profile content and visibility choices for an account.';


--
-- Name: COLUMN account_profiles.display_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.display_name IS 'Public display name, independent of the creator aliases used for build credits.';


--
-- Name: COLUMN account_profiles.links; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.links IS 'Published links, stored as a JSON array. Replace the tuple when editing links.';


--
-- Name: COLUMN account_profiles.hidden; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.hidden IS 'Whether to hide profile content. Aliases and build credits remain public.';


--
-- Name: COLUMN account_profiles.avatar_identity_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.account_profiles.avatar_identity_id IS 'Linked identity used for the avatar. Unlinking it clears this choice; writes check account ownership.';


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    consent_version text,
    consented_at timestamp with time zone,
    public_creator_id uuid DEFAULT gen_random_uuid() NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT accounts_consent_receipt_complete CHECK (((consent_version IS NULL) = (consented_at IS NULL))),
    CONSTRAINT accounts_revision_positive CHECK ((revision > 0))
);


--
-- Name: TABLE accounts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.accounts IS 'An internal caller independent of every external identity provider.';


--
-- Name: COLUMN accounts.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.accounts.id IS 'Internal account identifier; assigned when the account is first saved.';


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
-- Name: alembic_migration_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_migration_history (
    id bigint NOT NULL,
    revision character varying(64) NOT NULL,
    down_revisions character varying(64)[] NOT NULL,
    direction text NOT NULL,
    applied_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    duration_ms integer NOT NULL,
    script_sha256 character(64),
    release_version text NOT NULL,
    applied_from text NOT NULL,
    CONSTRAINT alembic_migration_history_direction_check CHECK ((direction = ANY (ARRAY['upgrade'::text, 'downgrade'::text, 'stamp'::text])))
);


--
-- Name: TABLE alembic_migration_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.alembic_migration_history IS 'Append-only history of upgrades, downgrades, and stamps. Entries commit with the migration transaction; stamps record revisions without executing their scripts.';


--
-- Name: COLUMN alembic_migration_history.revision; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.revision IS 'The revision this step moved onto or off of.';


--
-- Name: COLUMN alembic_migration_history.down_revisions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.down_revisions IS 'Parents of `revision`; more than one only at a branch merge.';


--
-- Name: COLUMN alembic_migration_history.direction; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.direction IS 'upgrade and downgrade ran the script; stamp asserted the revision without running it.';


--
-- Name: COLUMN alembic_migration_history.applied_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.applied_at IS 'clock_timestamp(), not now(): a whole `upgrade head` shares one transaction, so transaction time would stamp every step of a release identically.';


--
-- Name: COLUMN alembic_migration_history.duration_ms; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.duration_ms IS 'Wall time from the previous step of this run. The first row of a run also carries that run''s setup.';


--
-- Name: COLUMN alembic_migration_history.script_sha256; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.script_sha256 IS 'Digest of the revision file for this step, checked while the revision is in the current schema ancestry. NULL when the revision resolved to no file on disk.';


--
-- Name: COLUMN alembic_migration_history.release_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.release_version IS 'SQUID_BUILD_COMMIT_HASH of the image that ran it.';


--
-- Name: COLUMN alembic_migration_history.applied_from; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alembic_migration_history.applied_from IS 'Hostname of the process that ran it, which is the container id under Compose.';


--
-- Name: alembic_migration_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.alembic_migration_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.alembic_migration_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


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
-- Name: build_edit_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_edit_history (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    operation_id uuid NOT NULL,
    operation text NOT NULL,
    source text NOT NULL,
    actor_account_id integer,
    actor_system text,
    correlation_kind text,
    correlation_id text,
    before_state_id bigint,
    after_state_id bigint,
    CONSTRAINT build_history_account_positive CHECK (((actor_account_id IS NULL) OR (actor_account_id > 0))),
    CONSTRAINT build_history_actor_check CHECK ((num_nonnulls(actor_account_id, actor_system) = 1)),
    CONSTRAINT build_history_correlation_check CHECK (((correlation_kind IS NULL) = (correlation_id IS NULL))),
    CONSTRAINT build_history_source_check CHECK (((length(btrim(source)) > 0) AND ((actor_system IS NULL) OR (length(btrim(actor_system)) > 0)))),
    CONSTRAINT build_history_states_check CHECK ((((operation = ANY (ARRAY['baseline'::text, 'create'::text])) AND (before_state_id IS NULL) AND (after_state_id IS NOT NULL)) OR ((operation = 'delete'::text) AND (before_state_id IS NOT NULL) AND (after_state_id IS NULL)) OR ((operation = ANY (ARRAY['edit'::text, 'moderation'::text, 'recalculation'::text, 'schematic'::text, 'account_merge'::text])) AND (before_state_id IS NOT NULL) AND (after_state_id IS NOT NULL))))
);


--
-- Name: TABLE build_edit_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_edit_history IS 'An append-only operation whose states and actor survive live-record deletion.';


--
-- Name: build_edit_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.build_edit_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.build_edit_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: build_edit_pending; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_edit_pending (
    transaction_id bigint NOT NULL,
    build_id bigint NOT NULL,
    context jsonb NOT NULL,
    before_snapshot jsonb,
    before_revision bigint,
    CONSTRAINT build_edit_pending_before_check CHECK (((before_snapshot IS NULL) = (before_revision IS NULL)))
);


--
-- Name: TABLE build_edit_pending; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_edit_pending IS 'Transaction-local work removed by the deferred history trigger before commit.';


--
-- Name: build_history_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_history_states (
    id bigint NOT NULL,
    build_id bigint NOT NULL,
    captured_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    build_revision bigint NOT NULL,
    schema_version smallint NOT NULL,
    snapshot jsonb NOT NULL,
    CONSTRAINT build_history_states_revision_positive CHECK ((build_revision > 0)),
    CONSTRAINT build_history_states_schema_positive CHECK ((schema_version > 0)),
    CONSTRAINT build_history_states_snapshot_object CHECK ((jsonb_typeof(snapshot) = 'object'::text))
);


--
-- Name: TABLE build_history_states; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_history_states IS 'One observed build state, shared by adjacent history entries.';


--
-- Name: build_history_states_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.build_history_states ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.build_history_states_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


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

COMMENT ON TABLE public.build_links IS 'A URL associated with a build, such as an image, schematic, or world download.';


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
    CONSTRAINT build_schematics_block_count_check CHECK ((block_count <= bounding_volume)),
    CONSTRAINT build_schematics_bounding_volume_check CHECK ((bounding_volume = (((width)::bigint * height) * length))),
    CONSTRAINT build_schematics_license_check CHECK (((license_code IS NULL) OR (license_code = ANY (ARRAY['cc0_1_0'::text, 'cc_by_4_0'::text, 'cc_by_sa_4_0'::text, 'cc_by_nd_4_0'::text, 'cc_by_nc_4_0'::text, 'cc_by_nc_sa_4_0'::text, 'cc_by_nc_nd_4_0'::text])))),
    CONSTRAINT build_schematics_measurements_nonnegative CHECK (((width >= 0) AND (height >= 0) AND (length >= 0) AND (allocated_width >= 0) AND (allocated_height >= 0) AND (allocated_length >= 0) AND (block_count >= 0) AND (entity_count >= 0) AND (palette_size >= 0))),
    CONSTRAINT build_schematics_public_is_published CHECK (((visibility <> 'public_download'::text) OR (published_at IS NOT NULL))),
    CONSTRAINT build_schematics_publication_complete CHECK (((visibility <> 'public_download'::text) OR ((license_code IS NOT NULL) AND (rights_attested_at IS NOT NULL) AND (rights_attested_by_account_id IS NOT NULL)))),
    CONSTRAINT build_schematics_tight_within_allocated CHECK (((width <= allocated_width) AND (height <= allocated_height) AND (length <= allocated_length))),
    CONSTRAINT build_schematics_visibility_check CHECK ((visibility = ANY (ARRAY['legacy_unverified'::text, 'reviewer_only'::text, 'public_download'::text]))),
    CONSTRAINT build_schematics_withdrawn_after_published CHECK (((withdrawn_at IS NULL) OR ((published_at IS NOT NULL) AND (withdrawn_at >= published_at))))
);


--
-- Name: TABLE build_schematics; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_schematics IS 'One analyzed file attached to a build, with its publication policy.

Measurements and fingerprints are stored here for duplicate shortlisting. Fingerprint lookups
include analyzer_version because an engine upgrade can change the hashes. Reanalyzing an
attachment updates this evidence without changing its source bytes.';


--
-- Name: COLUMN build_schematics.file_sha256; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.file_sha256 IS 'Digest of the analyzed bytes, which may be shared by several builds.

Deletion is restricted while any attachment references the file.';


--
-- Name: COLUMN build_schematics.is_primary; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.is_primary IS 'Featured attachment used for generated previews, conversion, and timing measurements.';


--
-- Name: COLUMN build_schematics.original_filename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.original_filename IS 'Original display filename; ingestion may also use its extension as a fallback format hint.';


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

COMMENT ON COLUMN public.build_schematics.block_count IS 'Non-air block count, separate from the Door Rules cumulative volume.

The latter includes air pockets and needs hallway, frame, and hitbox review.';


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

COMMENT ON COLUMN public.build_schematics.fingerprint_structural IS 'Coarse translation-invariant hash used to shortlist files for pairwise comparison.

Matching values can include different block arrangements and do not establish duplication.';


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

COMMENT ON COLUMN public.build_schematics.analyzer_version IS 'Engine version that produced these fingerprints; comparisons must use the same version.';


--
-- Name: COLUMN build_schematics.analysis_schema_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_schematics.analysis_schema_version IS 'Application analysis format version, independent of the engine version.';


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

COMMENT ON COLUMN public.build_schematics.uploaded_by_account_id IS 'Account that supplied the file, which can differ from the account attesting publication rights.';


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
-- Name: COLUMN build_tag_assignments.display_unit_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.build_tag_assignments.display_unit_key IS 'Unit used for presentation; the stored numeric value uses the definition''s canonical unit.';


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
-- Name: TABLE build_vote_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_vote_sessions IS 'Link a build review to its target and recorded changes.';


--
-- Name: builds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.builds (
    id bigint NOT NULL,
    submission_status smallint NOT NULL,
    edited_time timestamp with time zone DEFAULT now() NOT NULL,
    extra_info jsonb DEFAULT '{}'::jsonb NOT NULL,
    width integer,
    height integer,
    depth integer,
    completion_time text,
    submission_time timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    category text NOT NULL,
    ai_generated boolean NOT NULL,
    version_spec text,
    embedding public.vector(1536),
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
    CONSTRAINT builds_category_check CHECK ((category = ANY (ARRAY['Door'::text, 'Extender'::text, 'Utility'::text, 'Entrance'::text, 'Other'::text]))),
    CONSTRAINT builds_display_name_valid CHECK (((display_name IS NULL) OR ((display_name = btrim(display_name)) AND (display_name <> ''::text) AND (char_length(display_name) <= 120)))),
    CONSTRAINT builds_lock_lease_complete CHECK ((((locked_at IS NULL) AND (lock_token IS NULL) AND (lock_expires_at IS NULL)) OR ((locked_at IS NOT NULL) AND (lock_token IS NOT NULL) AND (lock_expires_at IS NOT NULL)))),
    CONSTRAINT builds_revision_positive CHECK ((revision > 0)),
    CONSTRAINT builds_sponsor_address_length CHECK (((sponsor_address IS NULL) OR ((char_length(sponsor_address) >= 1) AND (char_length(sponsor_address) <= 255)))),
    CONSTRAINT builds_sponsor_description_length CHECK (((sponsor_description IS NULL) OR ((char_length(sponsor_description) >= 1) AND (char_length(sponsor_description) <= 500)))),
    CONSTRAINT builds_sponsor_display_name_length CHECK (((sponsor_display_name IS NULL) OR ((char_length(sponsor_display_name) >= 1) AND (char_length(sponsor_display_name) <= 80)))),
    CONSTRAINT builds_sponsor_projection_complete CHECK (((sponsor_installation_id IS NOT NULL) OR ((sponsor_display_name IS NULL) AND (sponsor_address IS NULL) AND (sponsor_description IS NULL) AND (sponsor_website_url IS NULL)))),
    CONSTRAINT builds_sponsor_website_valid CHECK (((sponsor_website_url IS NULL) OR (((char_length(sponsor_website_url) >= 1) AND (char_length(sponsor_website_url) <= 2048)) AND (sponsor_website_url ~ '^https?://'::text)))),
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
-- Name: COLUMN builds.width; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.builds.width IS 'Declared width in blocks; None means unknown.';


--
-- Name: COLUMN builds.completion_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.builds.completion_time IS 'Completion date as supplied by the submitter, including partial dates.';


--
-- Name: COLUMN builds.embedding; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.builds.embedding IS 'Application-owned semantic vector stored in the authoritative build row.';


--
-- Name: COLUMN builds.revision; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.builds.revision IS 'Optimistic version checked by explicit repository writes.';


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
-- Name: TABLE delete_log_vote_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.delete_log_vote_sessions IS 'Store the Discord message targeted by a deletion vote.';


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
    CONSTRAINT discord_posts_resource_kind_check CHECK ((resource_kind = ANY (ARRAY['build'::text, 'vote_session'::text, 'starboard_entry'::text, 'inference_run'::text, 'submission_draft'::text]))),
    CONSTRAINT discord_posts_surface_check CHECK ((surface = ANY (ARRAY['build_card'::text, 'build_review'::text, 'vote_card'::text, 'starboard_entry'::text, 'submission_status'::text])))
);


--
-- Name: TABLE discord_posts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.discord_posts IS 'A bot-owned Discord message displaying a build, vote, or other resource.';


--
-- Name: COLUMN discord_posts.channel_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.channel_id IS 'Copied from the message so the unique index can limit live posts per channel.';


--
-- Name: COLUMN discord_posts.resource_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.resource_kind IS 'The type of resource displayed by this message, used to select its renderer.';


--
-- Name: COLUMN discord_posts.resource_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.resource_key IS 'The resource''s identifier, such as a build ID or a starboard/message ID pair.';


--
-- Name: COLUMN discord_posts.surface; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.surface IS 'The presentation to use, such as a build card or a vote card.';


--
-- Name: COLUMN discord_posts.applied_revision; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.applied_revision IS 'The last rendered queue generation; a larger queued generation means an update is pending.';


--
-- Name: COLUMN discord_posts.rendered_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.rendered_at IS 'When a rendered update was last recorded; None until the first update.';


--
-- Name: COLUMN discord_posts.suppressed_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_posts.suppressed_at IS 'When an external deletion was recorded. Each renderer decides whether to publish a replacement.';


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
    CONSTRAINT discord_sync_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT discord_sync_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT discord_sync_queue_resource_kind_check CHECK ((resource_kind = ANY (ARRAY['build'::text, 'vote_session'::text, 'starboard_entry'::text, 'inference_run'::text, 'submission_draft'::text])))
);


--
-- Name: TABLE discord_sync_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.discord_sync_queue IS 'A pending refresh or deletion of a resource''s Discord posts.

Database triggers insert one row per resource and replace its action and
generation when another change arrives. They also clear any existing claim,
so a worker handling the older generation cannot acknowledge the new request.
Workers read the resource''s current state and delete the row on completion.

The generation is compared with each post''s applied revision. Several changes
can share one pending row; this table does not retain their history.';


--
-- Name: COLUMN discord_sync_queue.generation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_sync_queue.generation IS 'Sequence number used to compare requested work with a post''s applied revision.

The sequence continues after a queue row is deleted, so the next request does
not restart at a revision the post has already rendered.';


--
-- Name: COLUMN discord_sync_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_sync_queue.available_at IS 'Earliest time a worker may claim this row.

Retries move this time forward without changing enqueued_at, preserving queue
order and the age reported by health metrics.';


--
-- Name: COLUMN discord_sync_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.discord_sync_queue.claim_token IS 'Token generated for the current claim.

Completion and failure updates require a matching token. Enqueuing another
update clears it, preventing an older worker from acknowledging the new work.';


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
    CONSTRAINT domain_event_deliveries_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT domain_event_deliveries_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
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
-- Name: edit_operations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.edit_operations (
    id uuid NOT NULL,
    actor text NOT NULL,
    fingerprint bytea NOT NULL,
    revisions jsonb NOT NULL,
    changed boolean NOT NULL,
    final boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT edit_operations_fingerprint_length CHECK ((octet_length(fingerprint) = 32))
);


--
-- Name: edit_resource_leases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.edit_resource_leases (
    operation_id uuid NOT NULL,
    resource text NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


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

COMMENT ON COLUMN public.error_reports.reference IS 'Short reference shown to the user, usually the first 12 correlation-ID characters.

References are not unique. Multiple failures can share a correlation ID, and
different IDs can share a prefix; lookup also returns a match count.';


--
-- Name: COLUMN error_reports.occurred_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.occurred_at IS 'When the failure was captured.';


--
-- Name: COLUMN error_reports.expires_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.error_reports.expires_at IS 'When the report stops appearing in lookups and becomes eligible for deletion.';


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

COMMENT ON COLUMN public.error_reports.message IS 'The exception''s string form, available to callers authorized to read diagnostic details.';


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

COMMENT ON COLUMN public.error_reports.context IS 'Diagnostic context supplied by the caller; the storage service does not redact it.';


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

COMMENT ON COLUMN public.generic_vote_sessions.guild_id IS 'Owning guild used for emoji configuration and ballot role weights.

Guild polls may be created without one; network polls require an owner.
Presentation messages are attached separately.';


--
-- Name: COLUMN generic_vote_sessions.scope; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.generic_vote_sessions.scope IS 'Whether publication is limited to the owning guild or spans configured vote channels.';


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

COMMENT ON COLUMN public.idempotency_requests.principal IS 'Legacy copy of caller, still written by the repository for the retained database column.';


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

COMMENT ON TABLE public.media_artifacts IS 'Content-addressed normalized output, video thumbnail, or report metadata.';


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
-- Name: media_draft_references; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_draft_references (
    draft_id uuid NOT NULL,
    upload_id uuid NOT NULL,
    discarded boolean DEFAULT false NOT NULL
);


--
-- Name: TABLE media_draft_references; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_draft_references IS 'One independently discardable draft reference to a shared normalization job.';


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
    CONSTRAINT media_normalization_jobs_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT media_normalization_jobs_state_shape CHECK ((((status = 'pending'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'claimed'::text) AND (claimed_at IS NOT NULL) AND (claim_token IS NOT NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'completed'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NOT NULL) AND (dead_at IS NULL) AND (discarded_at IS NULL)) OR ((status = 'dead'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NOT NULL) AND (discarded_at IS NULL)) OR ((status = 'discarded'::text) AND (claimed_at IS NULL) AND (claim_token IS NULL) AND (completed_at IS NULL) AND (dead_at IS NULL) AND (discarded_at IS NOT NULL)))),
    CONSTRAINT media_normalization_jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'claimed'::text, 'completed'::text, 'dead'::text, 'discarded'::text])))
);


--
-- Name: TABLE media_normalization_jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_normalization_jobs IS 'A normalization request retained after completion, failure, or discard for cleanup and retries.';


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

COMMENT ON COLUMN public.messages.deleted_at IS 'When Discord reported deletion. The deletion handler retains this row and its content.';


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

COMMENT ON TABLE public.notification_deliveries IS 'A queued Discord DM retained across retries and after successful or terminal delivery.';


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
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT notification_profiles_revision_positive CHECK ((revision > 0))
);


--
-- Name: TABLE notification_profiles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.notification_profiles IS 'Channel preferences for an account. Consent is recorded on the account itself.';


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
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT notifications_kind_check CHECK ((kind = ANY (ARRAY['build_confirmed'::text, 'build_denied'::text, 'creator_build_confirmed'::text, 'record_gained'::text, 'staff_build_submitted'::text]))),
    CONSTRAINT notifications_revision_positive CHECK ((revision > 0))
);


--
-- Name: TABLE notifications; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.notifications IS 'An event-derived message for one recipient, shared by the web inbox and DM delivery.';


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

COMMENT ON COLUMN public.permission_audit_log.action IS 'Mutation name, such as `grant`, `revoke`, `role_create`, or `role_include`.';


--
-- Name: COLUMN permission_audit_log.reason; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_audit_log.reason IS 'Reason for the change; the service requires one for a forbid grant.';


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

COMMENT ON TABLE public.permission_epoch IS 'A shared counter used to invalidate permission caches.

Database triggers increment the counter and send `NOTIFY squid_permissions`
after permission changes. Watchers poll the counter and use notifications
to refresh sooner.';


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

COMMENT ON COLUMN public.permission_grants.granted_by_account_id IS 'Account that issued the grant, or None for a system operation.';


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

COMMENT ON COLUMN public.permission_grants.scope_guild_id IS 'Guild where the grant applies, or None for all guilds. Guild-scoped grants cannot satisfy global nodes.';


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

COMMENT ON COLUMN public.permission_role_assignments.granted_by_account_id IS 'Account that issued the assignment, or None for a system operation.';


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

COMMENT ON TABLE public.permission_role_patterns IS 'One included or excluded pattern in a role.

Exclusions remove matching nodes from this role''s contribution without denying
those nodes when another role or direct rule grants them.';


--
-- Name: COLUMN permission_role_patterns.pattern; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_role_patterns.pattern IS 'A node name, a `*`/`**` wildcard, or an `@tag` selector.';


--
-- Name: COLUMN permission_role_patterns.mode; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.permission_role_patterns.mode IS '1 to include or -1 to exclude; a role stores one mode for each pattern.';


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
    withdrawn boolean DEFAULT false NOT NULL,
    CONSTRAINT permission_roles_builtin_is_global CHECK (((builtin_key IS NULL) OR (guild_id IS NULL))),
    CONSTRAINT permission_roles_slug_format CHECK ((slug ~ '^[a-z][a-z0-9-]{1,31}$'::text))
);


--
-- Name: TABLE permission_roles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.permission_roles IS 'A named bundle of permission patterns and management settings.

Built-in pattern lists live in code. Stored patterns supplement those lists,
while this row supplies the assignment target, rank, and display metadata.';


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

COMMENT ON COLUMN public.permission_roles.rank IS 'Management rank used to decide who may edit the role. It does not affect permission resolution.';


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
-- Name: record_computation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_computation_runs (
    id bigint NOT NULL,
    rules_revision_id bigint NOT NULL,
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

COMMENT ON TABLE public.record_computation_runs IS 'A computation attempt for one build kind and version, including its publication state.';


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
    claimed_at timestamp with time zone,
    last_error text,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT record_recompute_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT record_recompute_queue_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_recompute_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);


--
-- Name: TABLE record_recompute_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_recompute_queue IS 'A durable request to recompute an affected record scope.';


--
-- Name: COLUMN record_recompute_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.record_recompute_queue.available_at IS 'Earliest next claim time; retry backoff advances it without changing the enqueue time.';


--
-- Name: COLUMN record_recompute_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.record_recompute_queue.claim_token IS 'Token identifying the current claim, cleared when new work replaces it.

Completion and failure updates match this token to preserve work enqueued
or reclaimed after the worker started.';


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
-- Name: record_titles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_titles (
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    base_title text NOT NULL,
    build_kind text NOT NULL,
    version_scope text NOT NULL,
    version_id smallint,
    category_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT record_titles_base_title_check CHECK ((base_title = ANY (ARRAY['first'::text, 'fastest'::text, 'smallest'::text, 'fastest_smallest'::text, 'smallest_fastest'::text]))),
    CONSTRAINT record_titles_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_titles_version_scope_check CHECK ((version_scope = ANY (ARRAY['all_time'::text, 'up_to_date'::text])))
);


--
-- Name: TABLE record_titles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_titles IS 'A stable public identity for a record title across rules revisions.';


--
-- Name: rules_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rules_revisions (
    id bigint NOT NULL,
    document_hash text NOT NULL,
    calculator_version text NOT NULL,
    formatter_version text NOT NULL,
    activated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE rules_revisions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.rules_revisions IS 'Calculator and formatter versions with a mutable activation timestamp.';


--
-- Name: rules_revisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.rules_revisions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.rules_revisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: runtime_setting_changes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.runtime_setting_changes (
    id bigint NOT NULL,
    actor_account_id integer,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    before jsonb NOT NULL,
    after jsonb NOT NULL
);


--
-- Name: TABLE runtime_setting_changes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.runtime_setting_changes IS 'An administrator''s committed change, including before and after snapshots.';


--
-- Name: COLUMN runtime_setting_changes.actor_account_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.runtime_setting_changes.actor_account_id IS 'Internal account that made the edit; retained as null if that account is deleted.';


--
-- Name: runtime_setting_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.runtime_setting_changes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: runtime_setting_changes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.runtime_setting_changes_id_seq OWNED BY public.runtime_setting_changes.id;


--
-- Name: runtime_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.runtime_settings (
    id integer NOT NULL,
    welcome_forward_chance double precision NOT NULL,
    welcome_enabled boolean NOT NULL,
    redstoner_enabled boolean NOT NULL,
    build_vote_pass_threshold smallint NOT NULL,
    build_vote_fail_threshold smallint NOT NULL,
    delete_vote_pass_threshold smallint NOT NULL,
    delete_vote_fail_threshold smallint NOT NULL,
    notification_retention_days integer NOT NULL,
    redstoner_starboard_author_id bigint NOT NULL,
    redstoner_starboard_channel_id bigint NOT NULL,
    redstoner_role_id bigint NOT NULL,
    redstoner_corner_channel_id bigint NOT NULL,
    redstoner_announcement_channel_id bigint NOT NULL,
    welcome_channel_id bigint NOT NULL,
    welcome_relay_channel_id bigint NOT NULL,
    version_tracker_channel_id bigint NOT NULL,
    build_log_channel_ids bigint[] NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT runtime_settings_chance CHECK (((welcome_forward_chance >= (0)::double precision) AND (welcome_forward_chance <= (1)::double precision))),
    CONSTRAINT runtime_settings_retention CHECK (((notification_retention_days >= 1) AND (notification_retention_days <= 3650))),
    CONSTRAINT runtime_settings_revision_positive CHECK ((revision > 0)),
    CONSTRAINT runtime_settings_singleton CHECK ((id = 1)),
    CONSTRAINT runtime_settings_thresholds CHECK (((build_vote_pass_threshold > 0) AND (build_vote_fail_threshold < 0) AND (delete_vote_pass_threshold > 0) AND (delete_vote_fail_threshold < 0)))
);


--
-- Name: TABLE runtime_settings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.runtime_settings IS 'Singleton containing global defaults and owner-guild community routing.';


--
-- Name: schematic_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schematic_files (
    sha256 text NOT NULL,
    byte_size integer NOT NULL,
    source_format text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    object_key text NOT NULL,
    CONSTRAINT schematic_files_object_key_check CHECK ((object_key = ((('schematics/'::text || "left"(sha256, 2)) || '/'::text) || sha256))),
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
    result_object_keys text[] DEFAULT '{}'::text[] NOT NULL,
    CONSTRAINT schematic_jobs_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT schematic_jobs_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL))),
    CONSTRAINT schematic_jobs_operation_check CHECK ((operation = ANY (ARRAY['capabilities'::text, 'analyze'::text, 'convert'::text, 'compare'::text, 'render'::text, 'simulate'::text, 'autostack'::text]))),
    CONSTRAINT schematic_jobs_single_terminal_state CHECK (((completed_at IS NULL) OR (dead_at IS NULL)))
);


--
-- Name: TABLE schematic_jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.schematic_jobs IS 'A durable native-engine request whose result remains available until retention expires.

Claim tokens reject stale worker updates. Binary output objects are retained alongside the row
for cleanup; input objects can be shared by multiple jobs.';


--
-- Name: COLUMN schematic_jobs.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_jobs.claim_token IS 'Token that must still match when the claiming worker records its outcome.';


--
-- Name: COLUMN schematic_jobs.result_object_keys; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_jobs.result_object_keys IS 'Result objects registered by worker claims, retained until job expiry.';


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
    cleanup_claimed_at timestamp with time zone,
    cleanup_claim_token uuid,
    CONSTRAINT schematic_preview_objects_cleanup_claim_pair CHECK (((cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL))),
    CONSTRAINT schematic_preview_objects_sha256_check CHECK (((sha256 IS NULL) OR (sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT schematic_preview_objects_size_positive CHECK ((byte_size > 0))
);


--
-- Name: TABLE schematic_preview_objects; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.schematic_preview_objects IS 'Durable upload and cleanup state for one generated preview object.';


--
-- Name: COLUMN schematic_preview_objects.cleanup_claimed_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_preview_objects.cleanup_claimed_at IS 'When cleanup reserved this unreferenced object for deletion.';


--
-- Name: COLUMN schematic_preview_objects.cleanup_claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_preview_objects.cleanup_claim_token IS 'Token required to finish cleanup; its presence prevents reuse or publication.';


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
    claim_token uuid,
    CONSTRAINT schematic_render_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT schematic_render_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);


--
-- Name: TABLE schematic_render_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.schematic_render_queue IS 'A durable request to render and publish one build''s primary schematic.';


--
-- Name: COLUMN schematic_render_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_render_queue.available_at IS 'Earliest time a worker may claim this job, including retry backoff.';


--
-- Name: COLUMN schematic_render_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.schematic_render_queue.claim_token IS 'Token that must still match when the claiming worker records its outcome.';


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

COMMENT ON COLUMN public.schematic_renders.recipe_hash IS 'Hash of the source and pack digests, framing, schematic dimensions, and analyzer version.';


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
    claimed_at timestamp with time zone,
    last_error text,
    dead_at timestamp with time zone,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT search_embedding_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT search_embedding_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);


--
-- Name: TABLE search_embedding_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_embedding_queue IS 'A durable request to embed a search document whose source hash changed.';


--
-- Name: COLUMN search_embedding_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_embedding_queue.available_at IS 'Earliest time a worker may claim this item; retries move it into the future.';


--
-- Name: COLUMN search_embedding_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_embedding_queue.claim_token IS 'Token generated for the current claim; stale workers cannot acknowledge a newer claim.';


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
    claimed_at timestamp with time zone,
    last_error text,
    dead_at timestamp with time zone,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claim_token uuid,
    CONSTRAINT search_projection_queue_action_check CHECK ((action = ANY (ARRAY['upsert'::text, 'delete'::text]))),
    CONSTRAINT search_projection_queue_attempts_nonnegative CHECK ((attempts >= 0)),
    CONSTRAINT search_projection_queue_claim_complete CHECK (((claimed_at IS NULL) = (claim_token IS NULL)))
);


--
-- Name: TABLE search_projection_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_projection_queue IS 'A durable request to refresh or delete a projected search resource.';


--
-- Name: COLUMN search_projection_queue.available_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_projection_queue.available_at IS 'Earliest time a worker may claim this item; retries move it into the future.';


--
-- Name: COLUMN search_projection_queue.claim_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.search_projection_queue.claim_token IS 'Token generated for the current claim; stale workers cannot acknowledge a newer claim.';


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
    locale text,
    revision bigint DEFAULT 1 NOT NULL,
    CONSTRAINT server_settings_revision_positive CHECK ((revision > 0))
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

COMMENT ON TABLE public.starboard_entries IS 'Current vote totals and rendering bookkeeping for one source message on one starboard.';


--
-- Name: starboard_origin_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.starboard_origin_messages (
    id bigint NOT NULL,
    author_is_bot boolean NOT NULL,
    is_nsfw boolean DEFAULT false NOT NULL,
    has_image boolean DEFAULT false NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL
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
-- Name: COLUMN starboard_sources.channel_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.starboard_sources.channel_id IS 'Source channel ID, or zero to include every channel in the source guild.';


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
    revision bigint DEFAULT 1 NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    CONSTRAINT starboards_age_check CHECK (((min_age_seconds >= 0) AND (max_age_seconds >= 0) AND ((max_age_seconds = 0) OR (min_age_seconds <= max_age_seconds)))),
    CONSTRAINT starboards_colour_check CHECK (((colour >= 0) AND (colour <= 16777215))),
    CONSTRAINT starboards_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT starboards_revision_positive CHECK ((revision > 0)),
    CONSTRAINT starboards_thresholds_check CHECK (((required > required_remove) AND (required <> 'Infinity'::double precision) AND (required <> '-Infinity'::double precision) AND (required <> 'NaN'::double precision) AND (required_remove <> 'Infinity'::double precision) AND (required_remove <> '-Infinity'::double precision) AND (required_remove <> 'NaN'::double precision)))
);


--
-- Name: TABLE starboards; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.starboards IS 'A named weighted-message board owned by one Discord guild.';


--
-- Name: COLUMN starboards.required; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.starboards.required IS 'Score at or above which an unposted entry qualifies for publication.';


--
-- Name: COLUMN starboards.required_remove; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.starboards.required_remove IS 'Score at or below which an existing entry no longer qualifies for publication.';


--
-- Name: COLUMN starboards.allow_bots; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.starboards.allow_bots IS 'Whether messages authored by bots may qualify; this does not authorize bot voters.';


--
-- Name: COLUMN starboards.max_age_seconds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.starboards.max_age_seconds IS 'Maximum source-message age in seconds; zero disables the upper age limit.';


--
-- Name: COLUMN starboards.archived; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.starboards.archived IS 'Hidden boards retain their ID and revision through creation undo and final deletion.';


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

COMMENT ON TABLE public.submission_draft_access IS 'An account''s role on a draft; creation grants one account the owner role.';


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
-- Name: submission_draft_schematics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_draft_schematics (
    draft_id uuid NOT NULL,
    source_id uuid NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    discarded boolean DEFAULT false NOT NULL,
    CONSTRAINT submission_draft_schematics_discarded_primary_check CHECK (((NOT discarded) OR (NOT is_primary)))
);


--
-- Name: TABLE submission_draft_schematics; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_draft_schematics IS 'One draft''s retained schematic reference and explicit primary selection.';


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
    preparation_issues jsonb DEFAULT '[]'::jsonb NOT NULL,
    preparation_retry_at timestamp with time zone,
    inferred boolean DEFAULT false NOT NULL,
    submission_actor_account_id integer,
    source_issues jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_files jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_messages jsonb DEFAULT '[]'::jsonb NOT NULL,
    inference_run_id uuid,
    CONSTRAINT submission_drafts_expiry_after_creation CHECK ((expires_at > created_at)),
    CONSTRAINT submission_drafts_inference_origin_check CHECK (((NOT inferred) OR (origin = 'discord'::text))),
    CONSTRAINT submission_drafts_installation_requires_paper CHECK (((source_installation_id IS NULL) OR (origin = 'paper'::text))),
    CONSTRAINT submission_drafts_origin_check CHECK ((origin = ANY (ARRAY['discord'::text, 'web'::text, 'cli'::text, 'paper'::text, 'fabric'::text]))),
    CONSTRAINT submission_drafts_preparation_issues_array CHECK ((jsonb_typeof(preparation_issues) = 'array'::text)),
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
-- Name: submission_finalization_inputs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_finalization_inputs (
    job_id uuid NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_finalization_inputs_payload_object_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT submission_finalization_inputs_payload_sha256_check CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: TABLE submission_finalization_inputs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_finalization_inputs IS 'Original normalized input retained independently of mutable execution state.';


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
    attempt_number integer DEFAULT 1 NOT NULL,
    requested_by_account_id integer,
    CONSTRAINT submission_finalization_jobs_attempt_number_positive CHECK ((attempt_number > 0)),
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

COMMENT ON TABLE public.submission_finalization_jobs IS 'A finalization attempt retained with its current worker claim and retry count.';


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

COMMENT ON TABLE public.submission_finalization_results IS 'Immutable build identity retained after successful finalization.';


--
-- Name: submission_inference_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_inference_runs (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    inputs jsonb NOT NULL,
    state text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    claim_token uuid,
    claim_expires_at timestamp with time zone,
    candidates jsonb NOT NULL,
    CONSTRAINT submission_inference_runs_state_check CHECK ((state = ANY (ARRAY['processing'::text, 'failed'::text, 'completed'::text])))
);


--
-- Name: TABLE submission_inference_runs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_inference_runs IS 'Private inference inputs, candidate results, and the current expiring worker claim.';


--
-- Name: submission_receipt_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_receipt_media (
    job_id uuid NOT NULL,
    upload_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE submission_receipt_media; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_receipt_media IS 'Normalized uploads retained by a committed submission receipt.';


--
-- Name: submission_revision_proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_revision_proposals (
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    requested_by_account_id integer NOT NULL,
    source_message_id bigint NOT NULL,
    facts jsonb NOT NULL,
    build_id integer,
    expected_revision integer,
    before jsonb NOT NULL,
    after jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    approved_by_account_id integer,
    applied_revision integer,
    CONSTRAINT submission_revision_proposals_receipt_check CHECK (((approved_by_account_id IS NULL) = (applied_revision IS NULL))),
    CONSTRAINT submission_revision_proposals_revision_check CHECK (((expected_revision IS NULL) OR (expected_revision >= 0))),
    CONSTRAINT submission_revision_proposals_target_check CHECK (((build_id IS NULL) = (expected_revision IS NULL)))
);


--
-- Name: TABLE submission_revision_proposals; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_revision_proposals IS 'A retained inference candidate, review snapshot, and atomic build revision receipt.';


--
-- Name: submission_schematic_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_schematic_sources (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    uploaded_by_account_id integer NOT NULL,
    filename text NOT NULL,
    sha256 text,
    byte_size bigint,
    state text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    analysis jsonb,
    last_error text,
    CONSTRAINT submission_schematic_sources_ready_check CHECK (((state <> 'ready'::text) OR (analysis IS NOT NULL))),
    CONSTRAINT submission_schematic_sources_sha_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT submission_schematic_sources_size_check CHECK (((byte_size IS NULL) OR ((byte_size > 0) AND (byte_size <= 16777216)))),
    CONSTRAINT submission_schematic_sources_state_check CHECK ((state = ANY (ARRAY['uploading'::text, 'pending'::text, 'ready'::text, 'rejected'::text, 'failed'::text, 'deleting'::text, 'deleted'::text]))),
    CONSTRAINT submission_schematic_sources_uploaded_check CHECK (((state <> ALL (ARRAY['pending'::text, 'ready'::text, 'rejected'::text])) OR ((sha256 IS NOT NULL) AND (byte_size IS NOT NULL))))
);


--
-- Name: TABLE submission_schematic_sources; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_schematic_sources IS 'Private source bytes tracked before upload and retained until reference-safe deletion.';


--
-- Name: submission_supplied_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_supplied_attachments (
    draft_id uuid NOT NULL,
    source_id uuid NOT NULL,
    filename text NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    discarded boolean DEFAULT false NOT NULL,
    history_managed boolean DEFAULT false NOT NULL,
    CONSTRAINT submission_supplied_attachments_kind_check CHECK ((kind = ANY (ARRAY['schematic'::text, 'image'::text, 'video'::text, 'unknown'::text]))),
    CONSTRAINT submission_supplied_attachments_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'ready'::text, 'failed'::text, 'discarded'::text])))
);


--
-- Name: TABLE submission_supplied_attachments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submission_supplied_attachments IS 'A supplied file retained before download and resolved only by registration or explicit discard.';


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
    revision bigint DEFAULT 1 NOT NULL,
    withdrawn boolean DEFAULT false NOT NULL,
    CONSTRAINT tag_definitions_authority_check CHECK ((authority = ANY (ARRAY['official'::text, 'user'::text]))),
    CONSTRAINT tag_definitions_display_order_check CHECK ((default_display_order >= 0)),
    CONSTRAINT tag_definitions_moderation_status_check CHECK ((moderation_status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'archived'::text]))),
    CONSTRAINT tag_definitions_non_numeric_unit_check CHECK (((value_type = 'numeric'::text) OR ((canonical_unit_key IS NULL) AND (default_display_unit_key IS NULL) AND (numeric_step IS NULL)))),
    CONSTRAINT tag_definitions_numeric_step_check CHECK (((numeric_step IS NULL) OR (numeric_step > (0)::numeric))),
    CONSTRAINT tag_definitions_query_name_format_check CHECK (((query_name IS NULL) OR (query_name ~ '^[a-z][a-z0-9_]{0,63}$'::text))),
    CONSTRAINT tag_definitions_record_operator_check CHECK (((record_operator IS NULL) OR (record_operator = ANY (ARRAY['present'::text, 'exact'::text, 'at_most'::text, 'at_least'::text])))),
    CONSTRAINT tag_definitions_record_operator_value_check CHECK ((((record_operator = 'present'::text) AND (value_type = 'none'::text)) OR ((record_operator = ANY (ARRAY['at_most'::text, 'at_least'::text])) AND (value_type = 'numeric'::text)) OR ((record_operator = 'exact'::text) AND (value_type <> 'none'::text)) OR (record_operator IS NULL))),
    CONSTRAINT tag_definitions_restriction_type_check CHECK ((((semantic_kind = 'restriction'::text) AND (restriction_type IS NOT NULL)) OR ((semantic_kind <> 'restriction'::text) AND (restriction_type IS NULL)))),
    CONSTRAINT tag_definitions_revision_positive CHECK ((revision > 0)),
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
-- Name: title_definition_facets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.title_definition_facets (
    definition_id bigint NOT NULL,
    facet_kind text NOT NULL,
    facet_id bigint NOT NULL,
    display_order smallint DEFAULT 0 NOT NULL,
    CONSTRAINT title_definition_facets_kind_check CHECK ((facet_kind = 'restriction'::text))
);


--
-- Name: TABLE title_definition_facets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.title_definition_facets IS 'A canonical taxonomy facet belonging to a record definition.';


--
-- Name: title_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.title_definitions (
    id bigint NOT NULL,
    rules_revision_id bigint NOT NULL,
    materialization_source text DEFAULT 'eager'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    title text NOT NULL,
    subtitle text,
    title_diagnostics jsonb DEFAULT '[]'::jsonb NOT NULL,
    title_id uuid NOT NULL,
    CONSTRAINT title_definitions_materialization_source_check CHECK ((materialization_source = ANY (ARRAY['eager'::text, 'seeded'::text, 'public_lookup'::text])))
);


--
-- Name: TABLE title_definitions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.title_definitions IS 'The wording of one stable record title under a rules revision.';


--
-- Name: title_definitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.title_definitions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.title_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: title_holder_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.title_holder_history (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    definition_id bigint NOT NULL,
    build_id bigint NOT NULL,
    predecessor_id bigint,
    held_from timestamp with time zone NOT NULL,
    held_until timestamp with time zone,
    metric_snapshot jsonb NOT NULL,
    CONSTRAINT title_holder_history_interval_check CHECK (((held_until IS NULL) OR (held_until >= held_from)))
);


--
-- Name: TABLE title_holder_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.title_holder_history IS 'A reconstructed interval in a definition''s beaten-record chronology.';


--
-- Name: title_holder_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.title_holder_history ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.title_holder_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: title_holders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.title_holders (
    result_id bigint NOT NULL,
    build_id bigint NOT NULL,
    rank smallint DEFAULT 1 NOT NULL,
    metric_snapshot jsonb NOT NULL,
    title text NOT NULL,
    subtitle text,
    completion_at timestamp with time zone,
    CONSTRAINT title_holders_rank_check CHECK ((rank > 0))
);


--
-- Name: TABLE title_holders; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.title_holders IS 'A co-holder of a resolved computed record.';


--
-- Name: title_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.title_results (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    definition_id bigint NOT NULL,
    status text NOT NULL,
    gap_reasons jsonb DEFAULT '{}'::jsonb NOT NULL,
    provisional_build_id bigint,
    history_complete boolean DEFAULT true NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT title_results_status_check CHECK ((status = ANY (ARRAY['resolved'::text, 'unresolved'::text, 'no_candidate'::text])))
);


--
-- Name: TABLE title_results; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.title_results IS 'The outcome for one definition in a computation run.';


--
-- Name: title_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.title_results ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.title_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


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

COMMENT ON TABLE public.verification_attempts IS 'Consecutive failed code redemptions for an external identity. A successful redemption resets the count.';


--
-- Name: verification_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verification_codes (
    id bigint NOT NULL,
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

COMMENT ON COLUMN public.verification_codes.reserved_token IS 'Digest of the token holding this code while its consent prompt is open.';


--
-- Name: COLUMN verification_codes.reserved_until; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.verification_codes.reserved_until IS 'When the reservation expires and the code becomes available again.';


--
-- Name: verification_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verification_codes_id_seq
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
    data_version integer,
    CONSTRAINT versions_edition_check CHECK ((edition = ANY (ARRAY['Java'::text, 'Bedrock'::text]))),
    CONSTRAINT versions_number_check CHECK (((major_version >= 0) AND (minor_version >= 0) AND (patch_number >= 0)))
);


--
-- Name: TABLE versions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.versions IS 'A version of Minecraft that a build is compatible with.';


--
-- Name: COLUMN versions.data_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.versions.data_version IS 'Minecraft''s world-format number, used when retargeting schematics.

None means no data version is recorded. Bedrock and early Java releases do not
have one.';


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
    guild_id bigint,
    label text,
    id bigint NOT NULL,
    CONSTRAINT vote_session_options_choice_check CHECK ((choice = ANY (ARRAY['approve'::text, 'deny'::text, 'generic'::text]))),
    CONSTRAINT vote_session_options_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision))),
    CONSTRAINT vote_session_options_position_check CHECK (("position" >= 0))
);


--
-- Name: TABLE vote_session_options; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.vote_session_options IS 'Ordered reaction options and positive weight multipliers captured for each vote session.';


--
-- Name: COLUMN vote_session_options.guild_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vote_session_options.guild_id IS 'Guild using this emoji alias, or None for an option shared across guilds.';


--
-- Name: vote_session_options_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.vote_session_options ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.vote_session_options_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


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
-- Name: COLUMN vote_sessions.fail_threshold; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vote_sessions.fail_threshold IS 'Negative net score required for denial, or None for a generic poll.';


--
-- Name: COLUMN vote_sessions.pass_threshold; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vote_sessions.pass_threshold IS 'Positive net score required for approval, or None for a generic poll.';


--
-- Name: COLUMN vote_sessions.result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vote_sessions.result IS 'Pending until closure; threshold outcomes are approved or denied, and other closures are cancelled.';


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
    guild_id bigint,
    account_id integer NOT NULL,
    option_alias_id bigint NOT NULL,
    CONSTRAINT votes_weight_check CHECK (((weight > (0)::double precision) AND (weight <> 'Infinity'::double precision) AND (weight <> 'NaN'::double precision)))
);


--
-- Name: TABLE votes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.votes IS 'One account''s selected option and cached positive weight in a session.';


--
-- Name: COLUMN votes.weight; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.votes.weight IS 'Cached positive magnitude; the option choice determines the sign used in threshold scoring.';


--
-- Name: COLUMN votes.guild_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.votes.guild_id IS 'The guild whose card the ballot was cast on, which decides the option''s emoji alias.';


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
-- Name: runtime_setting_changes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runtime_setting_changes ALTER COLUMN id SET DEFAULT nextval('public.runtime_setting_changes_id_seq'::regclass);


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
-- Name: alembic_migration_history alembic_migration_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_migration_history
    ADD CONSTRAINT alembic_migration_history_pkey PRIMARY KEY (id);


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
-- Name: build_edit_history build_edit_history_build_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_edit_history_build_id_key UNIQUE (build_id, operation_id);


--
-- Name: build_edit_history build_edit_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_edit_history_pkey PRIMARY KEY (id);


--
-- Name: build_edit_pending build_edit_pending_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_pending
    ADD CONSTRAINT build_edit_pending_pkey PRIMARY KEY (transaction_id, build_id);


--
-- Name: build_history_states build_history_states_build_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_history_states
    ADD CONSTRAINT build_history_states_build_id_key UNIQUE (build_id, id);


--
-- Name: build_history_states build_history_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_history_states
    ADD CONSTRAINT build_history_states_pkey PRIMARY KEY (id);


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
-- Name: edit_operations edit_operations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edit_operations
    ADD CONSTRAINT edit_operations_pkey PRIMARY KEY (id);


--
-- Name: edit_resource_leases edit_resource_leases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edit_resource_leases
    ADD CONSTRAINT edit_resource_leases_pkey PRIMARY KEY (operation_id, resource);


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
-- Name: media_draft_references media_draft_references_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_draft_references
    ADD CONSTRAINT media_draft_references_pkey PRIMARY KEY (draft_id, upload_id);


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
-- Name: record_computation_runs record_computation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_pkey PRIMARY KEY (id);


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
-- Name: record_titles record_titles_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_identity_key UNIQUE NULLS NOT DISTINCT (base_title, build_kind, version_scope, version_id, category_key);


--
-- Name: record_titles record_titles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_pkey PRIMARY KEY (public_id);


--
-- Name: rules_revisions rules_revisions_content_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rules_revisions
    ADD CONSTRAINT rules_revisions_content_key UNIQUE (document_hash, calculator_version, formatter_version);


--
-- Name: rules_revisions rules_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rules_revisions
    ADD CONSTRAINT rules_revisions_pkey PRIMARY KEY (id);


--
-- Name: runtime_setting_changes runtime_setting_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runtime_setting_changes
    ADD CONSTRAINT runtime_setting_changes_pkey PRIMARY KEY (id);


--
-- Name: runtime_settings runtime_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runtime_settings
    ADD CONSTRAINT runtime_settings_pkey PRIMARY KEY (id);


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
-- Name: submission_draft_schematics submission_draft_schematics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_schematics
    ADD CONSTRAINT submission_draft_schematics_pkey PRIMARY KEY (draft_id, source_id);


--
-- Name: submission_drafts submission_drafts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_pkey PRIMARY KEY (id);


--
-- Name: submission_finalization_inputs submission_finalization_inputs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_inputs
    ADD CONSTRAINT submission_finalization_inputs_pkey PRIMARY KEY (job_id);


--
-- Name: submission_finalization_jobs submission_finalization_jobs_draft_attempt_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_draft_attempt_key UNIQUE (draft_id, attempt_number);


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
-- Name: submission_inference_runs submission_inference_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_inference_runs
    ADD CONSTRAINT submission_inference_runs_pkey PRIMARY KEY (id);


--
-- Name: submission_receipt_media submission_receipt_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_receipt_media
    ADD CONSTRAINT submission_receipt_media_pkey PRIMARY KEY (job_id, upload_id);


--
-- Name: submission_revision_proposals submission_revision_proposals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_pkey PRIMARY KEY (id);


--
-- Name: submission_schematic_sources submission_schematic_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_schematic_sources
    ADD CONSTRAINT submission_schematic_sources_pkey PRIMARY KEY (id);


--
-- Name: submission_supplied_attachments submission_supplied_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_supplied_attachments
    ADD CONSTRAINT submission_supplied_attachments_pkey PRIMARY KEY (draft_id, source_id);


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
-- Name: title_definition_facets title_definition_facets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_definition_facets
    ADD CONSTRAINT title_definition_facets_pkey PRIMARY KEY (definition_id, facet_kind, facet_id);


--
-- Name: title_definitions title_definitions_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_identity_key UNIQUE (rules_revision_id, title_id);


--
-- Name: title_definitions title_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_pkey PRIMARY KEY (id);


--
-- Name: title_holder_history title_holder_history_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_identity_key UNIQUE (run_id, definition_id, build_id, held_from);


--
-- Name: title_holder_history title_holder_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_pkey PRIMARY KEY (id);


--
-- Name: title_holders title_holders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holders
    ADD CONSTRAINT title_holders_pkey PRIMARY KEY (result_id, build_id);


--
-- Name: title_results title_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_pkey PRIMARY KEY (id);


--
-- Name: title_results title_results_run_definition_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_run_definition_key UNIQUE (run_id, definition_id);


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
-- Name: versions versions_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_number_key UNIQUE (edition, major_version, minor_version, patch_number);


--
-- Name: versions versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.versions
    ADD CONSTRAINT versions_pkey PRIMARY KEY (id);


--
-- Name: vote_session_options vote_session_options_alias_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_alias_key UNIQUE NULLS NOT DISTINCT (vote_session_id, guild_id, emoji);


--
-- Name: vote_session_options vote_session_options_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_pkey PRIMARY KEY (id);


--
-- Name: vote_session_options vote_session_options_session_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_session_id_key UNIQUE (vote_session_id, id);


--
-- Name: vote_session_options vote_session_options_vote_session_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_vote_session_id_position_key UNIQUE NULLS NOT DISTINCT (vote_session_id, guild_id, "position");


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
-- Name: build_edit_history_timeline_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX build_edit_history_timeline_idx ON public.build_edit_history USING btree (build_id, id);


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
-- Name: edit_operations_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX edit_operations_expiry_idx ON public.edit_operations USING btree (expires_at);


--
-- Name: edit_resource_leases_resource_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX edit_resource_leases_resource_expiry_idx ON public.edit_resource_leases USING btree (resource, expires_at);


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

CREATE INDEX idx_builds_category ON public.builds USING btree (category);


--
-- Name: idx_builds_submission_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_builds_submission_time ON public.builds USING btree (submission_time DESC);


--
-- Name: ix_runtime_setting_changes_actor_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runtime_setting_changes_actor_account_id ON public.runtime_setting_changes USING btree (actor_account_id);


--
-- Name: ix_submission_drafts_inference_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_submission_drafts_inference_run_id ON public.submission_drafts USING btree (inference_run_id);


--
-- Name: ix_submission_inference_runs_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_submission_inference_runs_expires_at ON public.submission_inference_runs USING btree (expires_at);


--
-- Name: ix_submission_inference_runs_owner_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_submission_inference_runs_owner_account_id ON public.submission_inference_runs USING btree (owner_account_id);


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
-- Name: media_draft_references_upload_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX media_draft_references_upload_idx ON public.media_draft_references USING btree (upload_id);


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
-- Name: notifications_event_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifications_event_idx ON public.notifications USING btree (event_id);


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
-- Name: record_recompute_queue_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_recompute_queue_build_idx ON public.record_recompute_queue USING btree (build_id);


--
-- Name: record_recompute_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_recompute_queue_ready_idx ON public.record_recompute_queue USING btree (available_at) WHERE (claimed_at IS NULL);


--
-- Name: record_titles_category_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_titles_category_idx ON public.record_titles USING btree (build_kind, base_title, category_key);


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

CREATE INDEX search_embedding_queue_ready_idx ON public.search_embedding_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));


--
-- Name: search_projection_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_projection_queue_ready_idx ON public.search_projection_queue USING btree (available_at) WHERE ((claimed_at IS NULL) AND (dead_at IS NULL));


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

CREATE UNIQUE INDEX starboards_guild_name_key ON public.starboards USING btree (guild_id, lower(name)) WHERE (NOT archived);


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
-- Name: submission_draft_schematics_one_primary; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX submission_draft_schematics_one_primary ON public.submission_draft_schematics USING btree (draft_id) WHERE is_primary;


--
-- Name: submission_draft_schematics_source_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_draft_schematics_source_idx ON public.submission_draft_schematics USING btree (source_id);


--
-- Name: submission_drafts_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_actor_idx ON public.submission_drafts USING btree (submission_actor_account_id) WHERE (submission_actor_account_id IS NOT NULL);


--
-- Name: submission_drafts_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_expiry_idx ON public.submission_drafts USING btree (expires_at) WHERE (status = ANY (ARRAY['editing'::text, 'processing'::text, 'needs_attention'::text]));


--
-- Name: submission_drafts_inferred_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_inferred_expiry_idx ON public.submission_drafts USING btree (expires_at) WHERE inferred;


--
-- Name: submission_drafts_owner_updated_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_owner_updated_idx ON public.submission_drafts USING btree (owner_account_id, updated_at);


--
-- Name: submission_drafts_preparation_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_preparation_ready_idx ON public.submission_drafts USING btree (preparation_retry_at) WHERE (preparation_retry_at IS NOT NULL);


--
-- Name: submission_drafts_source_installation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_drafts_source_installation_idx ON public.submission_drafts USING btree (source_installation_id) WHERE (source_installation_id IS NOT NULL);


--
-- Name: submission_finalization_jobs_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_finalization_jobs_actor_idx ON public.submission_finalization_jobs USING btree (requested_by_account_id) WHERE (requested_by_account_id IS NOT NULL);


--
-- Name: submission_finalization_jobs_attention_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_finalization_jobs_attention_idx ON public.submission_finalization_jobs USING btree (attention_at) WHERE (status = 'needs_attention'::text);


--
-- Name: submission_finalization_jobs_one_active_attempt; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX submission_finalization_jobs_one_active_attempt ON public.submission_finalization_jobs USING btree (draft_id) WHERE (status = ANY (ARRAY['pending'::text, 'claimed'::text]));


--
-- Name: submission_finalization_jobs_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_finalization_jobs_ready_idx ON public.submission_finalization_jobs USING btree (available_at) WHERE (status = ANY (ARRAY['pending'::text, 'claimed'::text]));


--
-- Name: submission_finalization_results_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_finalization_results_build_idx ON public.submission_finalization_results USING btree (build_id);


--
-- Name: submission_receipt_media_upload_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_receipt_media_upload_idx ON public.submission_receipt_media USING btree (upload_id);


--
-- Name: submission_revision_proposals_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_revision_proposals_actor_idx ON public.submission_revision_proposals USING btree (requested_by_account_id);


--
-- Name: submission_revision_proposals_approver_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_revision_proposals_approver_idx ON public.submission_revision_proposals USING btree (approved_by_account_id);


--
-- Name: submission_revision_proposals_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_revision_proposals_build_idx ON public.submission_revision_proposals USING btree (build_id);


--
-- Name: submission_revision_proposals_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_revision_proposals_expiry_idx ON public.submission_revision_proposals USING btree (expires_at);


--
-- Name: submission_revision_proposals_owner_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_revision_proposals_owner_idx ON public.submission_revision_proposals USING btree (owner_account_id);


--
-- Name: submission_revision_proposals_run_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_revision_proposals_run_idx ON public.submission_revision_proposals USING btree (run_id);


--
-- Name: submission_revision_proposals_source_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_revision_proposals_source_idx ON public.submission_revision_proposals USING btree (source_message_id);


--
-- Name: submission_schematic_sources_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_schematic_sources_actor_idx ON public.submission_schematic_sources USING btree (uploaded_by_account_id);


--
-- Name: submission_schematic_sources_cleanup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_schematic_sources_cleanup_idx ON public.submission_schematic_sources USING btree (updated_at) WHERE (state <> 'deleted'::text);


--
-- Name: submission_schematic_sources_owner_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_schematic_sources_owner_idx ON public.submission_schematic_sources USING btree (owner_account_id);


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
-- Name: title_definition_facets_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX title_definition_facets_lookup_idx ON public.title_definition_facets USING btree (facet_kind, facet_id);


--
-- Name: title_definitions_title_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX title_definitions_title_idx ON public.title_definitions USING btree (title_id);


--
-- Name: title_holder_history_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX title_holder_history_build_idx ON public.title_holder_history USING btree (build_id);


--
-- Name: title_holder_history_definition_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX title_holder_history_definition_idx ON public.title_holder_history USING btree (definition_id, held_from);


--
-- Name: title_holders_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX title_holders_build_idx ON public.title_holders USING btree (build_id);


--
-- Name: title_results_definition_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX title_results_definition_idx ON public.title_results USING btree (definition_id);


--
-- Name: title_results_provisional_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX title_results_provisional_build_idx ON public.title_results USING btree (provisional_build_id);


--
-- Name: verification_codes_valid_code_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX verification_codes_valid_code_key ON public.verification_codes USING btree (code) WHERE valid;


--
-- Name: vote_sessions_author_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vote_sessions_author_idx ON public.vote_sessions USING btree (author_account_id);


--
-- Name: votes_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX votes_account_idx ON public.votes USING btree (account_id);


--
-- Name: votes_option_alias_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX votes_option_alias_idx ON public.votes USING btree (vote_session_id, option_alias_id);


--
-- Name: web_sessions_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX web_sessions_account_idx ON public.web_sessions USING btree (account_id);


--
-- Name: web_sessions_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX web_sessions_active_idx ON public.web_sessions USING btree (expires_at) WHERE (revoked_at IS NULL);


--
-- Name: build_creators build_creators_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_creators_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_creators build_creators_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_creators_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_creators build_creators_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_creators_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_creators build_creators_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_creators_reject_truncate BEFORE TRUNCATE ON public.build_creators FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_edit_pending build_edit_finalize; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER build_edit_finalize AFTER INSERT ON public.build_edit_pending DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.finish_build_edit_trigger();


--
-- Name: build_edit_history build_edit_history_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_edit_history_immutable BEFORE DELETE OR UPDATE OR TRUNCATE ON public.build_edit_history FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();


--
-- Name: build_history_states build_history_states_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_history_states_immutable BEFORE DELETE OR UPDATE OR TRUNCATE ON public.build_history_states FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();


--
-- Name: build_links build_links_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_links_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_links build_links_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_links_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_links build_links_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_links_reject_truncate BEFORE TRUNCATE ON public.build_links FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_schematics build_schematics_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_schematics_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_schematics FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_schematics build_schematics_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_schematics_reject_truncate BEFORE TRUNCATE ON public.build_schematics FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_source_messages build_source_messages_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_source_messages_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_source_messages FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_source_messages build_source_messages_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_source_messages_reject_truncate BEFORE TRUNCATE ON public.build_source_messages FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_tag_assignments build_tag_assignments_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_tag_assignments_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_tag_assignments build_tag_assignments_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_tag_assignments_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_tag_assignments build_tag_assignments_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_tag_assignments_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_tag_assignments build_tag_assignments_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_tag_assignments_reject_truncate BEFORE TRUNCATE ON public.build_tag_assignments FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_versions build_versions_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_versions_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_versions build_versions_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_versions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: build_versions build_versions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_versions build_versions_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_versions_reject_truncate BEFORE TRUNCATE ON public.build_versions FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: build_vote_sessions build_vote_sessions_delete_vote_session; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_vote_sessions_delete_vote_session AFTER DELETE ON public.build_vote_sessions FOR EACH ROW EXECUTE FUNCTION public.delete_vote_session_after_build_vote_session_delete();


--
-- Name: build_vote_sessions build_vote_sessions_kind_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER build_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.build_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();


--
-- Name: builds builds_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: builds builds_category_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER builds_category_subtype_check AFTER INSERT OR UPDATE OF id, category ON public.builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();


--
-- Name: builds builds_emit_domain_event; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_emit_domain_event AFTER INSERT OR UPDATE OF submission_status ON public.builds FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();


--
-- Name: builds builds_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_enqueue_discord_sync AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: builds builds_enqueue_discord_sync_on_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_enqueue_discord_sync_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN (((to_jsonb(old.*) - ARRAY['embedding'::text, 'locked_at'::text, 'lock_token'::text, 'lock_expires_at'::text]) IS DISTINCT FROM (to_jsonb(new.*) - ARRAY['embedding'::text, 'locked_at'::text, 'lock_token'::text, 'lock_expires_at'::text]))) EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: builds builds_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: builds builds_enqueue_search_on_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_enqueue_search_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN (((to_jsonb(old.*) - ARRAY['embedding'::text, 'locked_at'::text, 'lock_token'::text, 'lock_expires_at'::text]) IS DISTINCT FROM (to_jsonb(new.*) - ARRAY['embedding'::text, 'locked_at'::text, 'lock_token'::text, 'lock_expires_at'::text]))) EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: builds builds_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_reject_truncate BEFORE TRUNCATE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: creator_aliases creator_aliases_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER creator_aliases_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.creator_aliases FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: creator_aliases creator_aliases_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER creator_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.creator_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: creator_aliases creator_aliases_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER creator_aliases_reject_truncate BEFORE TRUNCATE ON public.creator_aliases FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: delete_log_vote_sessions delete_log_vote_sessions_kind_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER delete_log_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.delete_log_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();


--
-- Name: door_timing_variants door_timing_variants_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER door_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: door_timing_variants door_timing_variants_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER door_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: doors doors_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: doors doors_category_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER doors_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.doors DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();


--
-- Name: doors doors_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: doors doors_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: doors doors_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_reject_truncate BEFORE TRUNCATE ON public.doors FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: entrances entrances_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER entrances_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.entrances FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: entrances entrances_category_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER entrances_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.entrances DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();


--
-- Name: entrances entrances_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER entrances_reject_truncate BEFORE TRUNCATE ON public.entrances FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: extender_timing_variants extender_timing_variants_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extender_timing_variants_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: extender_timing_variants extender_timing_variants_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extender_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: extenders extenders_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extenders_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: extenders extenders_category_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER extenders_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.extenders DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();


--
-- Name: extenders extenders_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extenders_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();


--
-- Name: extenders extenders_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extenders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: extenders extenders_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extenders_reject_truncate BEFORE TRUNCATE ON public.extenders FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: generic_vote_sessions generic_vote_sessions_kind_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER generic_vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.generic_vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();


--
-- Name: messages messages_enqueue_starboard_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER messages_enqueue_starboard_sync AFTER UPDATE OF deleted_at ON public.messages FOR EACH ROW WHEN ((old.deleted_at IS DISTINCT FROM new.deleted_at)) EXECUTE FUNCTION public.enqueue_starboard_sync();


--
-- Name: other_builds other_builds_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER other_builds_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.other_builds FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: other_builds other_builds_category_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER other_builds_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.other_builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();


--
-- Name: other_builds other_builds_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER other_builds_reject_truncate BEFORE TRUNCATE ON public.other_builds FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: permission_grants permission_grants_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_grants_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_grants FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_role_assignments permission_role_assignments_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_role_assignments_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_role_assignments FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_role_includes permission_role_includes_acyclic; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER permission_role_includes_acyclic AFTER INSERT OR UPDATE ON public.permission_role_includes NOT DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION public.check_permission_role_include_cycle();


--
-- Name: permission_role_includes permission_role_includes_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_role_includes_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_role_includes FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_role_patterns permission_role_patterns_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_role_patterns_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_role_patterns FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: permission_roles permission_roles_bump_epoch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER permission_roles_bump_epoch BEFORE INSERT OR DELETE OR UPDATE ON public.permission_roles FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch();


--
-- Name: record_computation_runs record_computation_runs_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER record_computation_runs_enqueue_search AFTER INSERT OR DELETE OR UPDATE OF is_active ON public.record_computation_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();


--
-- Name: starboard_entries starboard_entries_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER starboard_entries_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE OF score ON public.starboard_entries FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();


--
-- Name: starboards starboards_enqueue_discord_sync; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER starboards_enqueue_discord_sync AFTER UPDATE ON public.starboards FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();


--
-- Name: submission_drafts submission_drafts_enqueue_status; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER submission_drafts_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_drafts FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();


--
-- Name: submission_finalization_inputs submission_finalization_inputs_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER submission_finalization_inputs_immutable BEFORE UPDATE ON public.submission_finalization_inputs FOR EACH ROW EXECUTE FUNCTION public.reject_submission_finalization_input_update();


--
-- Name: submission_supplied_attachments submission_intake_enqueue_status; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER submission_intake_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_supplied_attachments FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();


--
-- Name: submission_inference_runs submission_runs_enqueue_status; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER submission_runs_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_inference_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();


--
-- Name: tag_aliases tag_aliases_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER tag_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: tag_definitions tag_definitions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER tag_definitions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_definitions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: title_holders title_holders_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER title_holders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_holders FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();


--
-- Name: title_results title_results_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER title_results_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_results FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();


--
-- Name: utilities utilities_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER utilities_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.utilities FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: utilities utilities_category_subtype_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER utilities_category_subtype_check AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.utilities DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();


--
-- Name: utilities utilities_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER utilities_reject_truncate BEFORE TRUNCATE ON public.utilities FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: versions versions_capture_edit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER versions_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();


--
-- Name: versions versions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: versions versions_reject_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER versions_reject_truncate BEFORE TRUNCATE ON public.versions FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();


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
-- Name: build_edit_history build_history_after_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_history_after_fkey FOREIGN KEY (build_id, after_state_id) REFERENCES public.build_history_states(build_id, id);


--
-- Name: build_edit_history build_history_before_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_history_before_fkey FOREIGN KEY (build_id, before_state_id) REFERENCES public.build_history_states(build_id, id);


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
-- Name: media_artifacts media_artifacts_object_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_object_key_fkey FOREIGN KEY (object_key) REFERENCES public.media_artifact_objects(object_key);


--
-- Name: media_artifacts media_artifacts_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_artifacts
    ADD CONSTRAINT media_artifacts_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE CASCADE;


--
-- Name: media_draft_references media_draft_references_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_draft_references
    ADD CONSTRAINT media_draft_references_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;


--
-- Name: media_draft_references media_draft_references_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_draft_references
    ADD CONSTRAINT media_draft_references_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE RESTRICT;


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
-- Name: record_computation_runs record_computation_runs_rules_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_rules_revision_id_fkey FOREIGN KEY (rules_revision_id) REFERENCES public.rules_revisions(id) ON DELETE RESTRICT;


--
-- Name: record_computation_runs record_computation_runs_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_computation_runs
    ADD CONSTRAINT record_computation_runs_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;


--
-- Name: record_recompute_queue record_recompute_queue_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_recompute_queue
    ADD CONSTRAINT record_recompute_queue_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: record_titles record_titles_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;


--
-- Name: runtime_setting_changes runtime_setting_changes_actor_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runtime_setting_changes
    ADD CONSTRAINT runtime_setting_changes_actor_account_id_fkey FOREIGN KEY (actor_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


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
-- Name: starboard_origin_messages starboard_origin_messages_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.starboard_origin_messages
    ADD CONSTRAINT starboard_origin_messages_id_fkey FOREIGN KEY (id) REFERENCES public.messages(id) ON DELETE CASCADE;


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
-- Name: submission_draft_schematics submission_draft_schematics_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_schematics
    ADD CONSTRAINT submission_draft_schematics_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;


--
-- Name: submission_draft_schematics submission_draft_schematics_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_draft_schematics
    ADD CONSTRAINT submission_draft_schematics_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.submission_schematic_sources(id) ON DELETE RESTRICT;


--
-- Name: submission_drafts submission_drafts_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: submission_drafts submission_drafts_submission_actor_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_drafts
    ADD CONSTRAINT submission_drafts_submission_actor_account_id_fkey FOREIGN KEY (submission_actor_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_finalization_inputs submission_finalization_inputs_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_inputs
    ADD CONSTRAINT submission_finalization_inputs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.submission_finalization_jobs(id) ON DELETE CASCADE;


--
-- Name: submission_finalization_jobs submission_finalization_jobs_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;


--
-- Name: submission_finalization_jobs submission_finalization_jobs_requested_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_jobs
    ADD CONSTRAINT submission_finalization_jobs_requested_by_account_id_fkey FOREIGN KEY (requested_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_finalization_results submission_finalization_results_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_finalization_results
    ADD CONSTRAINT submission_finalization_results_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.submission_finalization_jobs(id) ON DELETE CASCADE;


--
-- Name: submission_inference_runs submission_inference_runs_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_inference_runs
    ADD CONSTRAINT submission_inference_runs_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_receipt_media submission_receipt_media_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_receipt_media
    ADD CONSTRAINT submission_receipt_media_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.submission_finalization_results(job_id) ON DELETE CASCADE;


--
-- Name: submission_receipt_media submission_receipt_media_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_receipt_media
    ADD CONSTRAINT submission_receipt_media_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.media_uploads(id) ON DELETE RESTRICT;


--
-- Name: submission_revision_proposals submission_revision_proposals_approved_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_approved_by_account_id_fkey FOREIGN KEY (approved_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_revision_proposals submission_revision_proposals_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;


--
-- Name: submission_revision_proposals submission_revision_proposals_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_revision_proposals submission_revision_proposals_requested_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_revision_proposals
    ADD CONSTRAINT submission_revision_proposals_requested_by_account_id_fkey FOREIGN KEY (requested_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_schematic_sources submission_schematic_sources_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_schematic_sources
    ADD CONSTRAINT submission_schematic_sources_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_schematic_sources submission_schematic_sources_uploaded_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_schematic_sources
    ADD CONSTRAINT submission_schematic_sources_uploaded_by_account_id_fkey FOREIGN KEY (uploaded_by_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: submission_supplied_attachments submission_supplied_attachments_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_supplied_attachments
    ADD CONSTRAINT submission_supplied_attachments_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;


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
-- Name: title_definition_facets title_definition_facets_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_definition_facets
    ADD CONSTRAINT title_definition_facets_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE CASCADE;


--
-- Name: title_definition_facets title_definition_facets_facet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_definition_facets
    ADD CONSTRAINT title_definition_facets_facet_id_fkey FOREIGN KEY (facet_id) REFERENCES public.tag_definitions(id) ON DELETE CASCADE;


--
-- Name: title_definitions title_definitions_rules_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_rules_revision_id_fkey FOREIGN KEY (rules_revision_id) REFERENCES public.rules_revisions(id) ON DELETE RESTRICT;


--
-- Name: title_definitions title_definitions_title_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_title_id_fkey FOREIGN KEY (title_id) REFERENCES public.record_titles(public_id) ON DELETE RESTRICT;


--
-- Name: title_holder_history title_holder_history_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;


--
-- Name: title_holder_history title_holder_history_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE RESTRICT;


--
-- Name: title_holder_history title_holder_history_predecessor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_predecessor_id_fkey FOREIGN KEY (predecessor_id) REFERENCES public.title_holder_history(id) ON DELETE SET NULL;


--
-- Name: title_holder_history title_holder_history_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holder_history
    ADD CONSTRAINT title_holder_history_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.record_computation_runs(id) ON DELETE CASCADE;


--
-- Name: title_holders title_holders_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holders
    ADD CONSTRAINT title_holders_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE RESTRICT;


--
-- Name: title_holders title_holders_result_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_holders
    ADD CONSTRAINT title_holders_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.title_results(id) ON DELETE CASCADE;


--
-- Name: title_results title_results_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.title_definitions(id) ON DELETE RESTRICT;


--
-- Name: title_results title_results_provisional_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_provisional_build_id_fkey FOREIGN KEY (provisional_build_id) REFERENCES public.builds(id) ON DELETE SET NULL;


--
-- Name: title_results title_results_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.title_results
    ADD CONSTRAINT title_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.record_computation_runs(id) ON DELETE CASCADE;


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
-- Name: votes votes_guild_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE RESTRICT;


--
-- Name: votes votes_option_alias_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_option_alias_fkey FOREIGN KEY (vote_session_id, option_alias_id) REFERENCES public.vote_session_options(vote_session_id, id);


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

