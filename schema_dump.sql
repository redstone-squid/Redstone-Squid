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

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('build', target_build_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
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
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        VALUES (
            'record',
            'result:' || target_result_id::text,
            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
            now()
        )
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'record_result_holders' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END;
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        VALUES ('record', 'result:' || target_result_id::text, 'upsert', now())
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSE
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'record', 'result:' || rr.id::text, 'upsert', now()
        FROM public.record_results rr
        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: enqueue_legacy_record_search_projection(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_legacy_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_record_id bigint;
    target_action text := 'upsert';
BEGIN
    target_record_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.record_id ELSE NEW.record_id END;
    IF TG_OP = 'DELETE' THEN
        target_action := 'delete';
    END IF;
    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('record', 'legacy-smallest:' || target_record_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;
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
        WHEN 'restrictions' THEN 'restriction'
        WHEN 'restriction_aliases' THEN 'restriction'
        WHEN 'types' THEN 'type'
        WHEN 'users' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'restriction_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.restriction_id ELSE NEW.restriction_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'restriction_aliases' THEN
        target_action := 'delete';
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('metadata', target_kind || ':' || target_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;

    IF TG_TABLE_NAME IN ('restrictions', 'restriction_aliases') THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', br.build_id::text, 'upsert', now()
        FROM public.build_restrictions br
        WHERE br.restriction_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'types' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bt.build_id::text, 'upsert', now()
        FROM public.build_types bt
        WHERE bt.type_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'users' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bc.build_id::text, 'upsert', now()
        FROM public.build_creators bc
        WHERE bc.user_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bv.build_id::text, 'upsert', now()
        FROM public.build_versions bv
        WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: find_restriction_ids(text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.find_restriction_ids(search_terms text[]) RETURNS TABLE(id smallint, build_category text, name text, type text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT *
    FROM (
        SELECT r.id, r.build_category, r.name, r.type  -- prevent collision with the TABLE above
        FROM restrictions r
        WHERE r.name = ANY(search_terms)

        UNION

        SELECT restriction_id, r.build_category, alias, r.type
        FROM restriction_aliases JOIN restrictions r ON restriction_id = r.id
        WHERE alias = ANY(search_terms)
    ) s;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    server_id bigint NOT NULL,
    build_id bigint,
    channel_id bigint,
    id bigint NOT NULL,
    updated_at timestamp with time zone,
    purpose text NOT NULL,
    content text,
    author_id bigint NOT NULL,
    vote_session_id bigint
);


--
-- Name: TABLE messages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.messages IS 'A message associated with a build or vote session.';


--
-- Name: COLUMN messages.purpose; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.purpose IS 'The reason why the message is stored in the database';


--
-- Name: get_outdated_messages(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_outdated_messages(server_id_input bigint) RETURNS SETOF public.messages
    LANGUAGE plpgsql
    AS $$begin
    return query select messages.*
    from messages join builds
    on (messages.submission_id = builds.submission_id)
    where messages.last_updated < builds.last_update
    and messages.server_id = server_id_input
    and builds.submission_status = 1;  -- accepted
  end;$$;


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
-- Name: builds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.builds (
    id bigint NOT NULL,
    submission_status smallint NOT NULL,
    edited_time timestamp with time zone DEFAULT now(),
    record_category text,
    extra_info jsonb DEFAULT '{}'::jsonb NOT NULL,
    width integer,
    height integer,
    depth integer,
    completion_time text,
    submission_time timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    category text,
    submitter_id bigint NOT NULL,
    ai_generated boolean NOT NULL,
    original_message_id bigint,
    version_spec text,
    embedding public.vector(1536),
    is_locked boolean DEFAULT false NOT NULL,
    locked_at timestamp with time zone,
    completion_at timestamp with time zone,
    completion_evidence text,
    description text,
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

COMMENT ON COLUMN public.builds.embedding IS 'This is not actually being used. See "vecs"."builds" instead';


--
-- Name: get_unsent_builds(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_unsent_builds(server_id_input bigint) RETURNS SETOF public.builds
    LANGUAGE plpgsql
    AS $$
  begin
    return query select *
    from builds
    where id not in (
      select build_id
      from messages
      where server_id = server_id_input
      )
    and submission_status = 1;  -- accepted
  end;
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
-- Name: rebuild_smallest_door_records(); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.rebuild_smallest_door_records()
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- 1. Take an exclusive lock so readers don’t see half a table.
    LOCK TABLE public.smallest_door_records IN ACCESS EXCLUSIVE MODE;

    -- 2. Wipe the current contents.
    TRUNCATE TABLE public.smallest_door_records;

    -- 3. Re-insert from scratch with the same query used during creation.
    WITH base AS (
        SELECT
            b.id   AS build_id,
            d.orientation,
            d.door_width,
            d.door_height,
            COALESCE(d.door_depth, 1)               AS door_depth,
            COALESCE(
                ARRAY_AGG(DISTINCT t.name ORDER BY t.name)
                    FILTER (WHERE t.name IS NOT NULL),
                ARRAY[]::text[]
            ) AS types,
            COALESCE(
                ARRAY_AGG(DISTINCT r.name ORDER BY r.name)
                    FILTER (WHERE r.name IS NOT NULL),
                ARRAY[]::text[]
            ) AS restrictions,
            b.width * b.height * COALESCE(b.depth, 1) AS volume
        FROM   public.builds             b
        JOIN   public.doors              d  ON d.build_id = b.id
        LEFT   JOIN public.build_types   bt ON bt.build_id = b.id
        LEFT   JOIN public.types         t  ON t.id = bt.type_id
        LEFT   JOIN public.build_restrictions br ON br.build_id = b.id
        LEFT   JOIN public.restrictions  r  ON r.id = br.restriction_id
        WHERE  b.submission_status = 1
          AND  b.category = 'Door'
          AND  b.width IS NOT NULL
          AND  b.height IS NOT NULL
          AND  b.depth IS NOT NULL
        GROUP  BY b.id, d.orientation, d.door_width, d.door_height, d.door_depth
    ), exploded AS (
        SELECT  b.*,
                ps AS restriction_subset
        FROM    base b
        CROSS   JOIN LATERAL public.power_set_max(b.restrictions, 8) ps
    ), ranked AS (
        SELECT  *,
                ROW_NUMBER() OVER (
                    PARTITION BY types,
                                 orientation, door_width,
                                 door_height, door_depth,
                                 restriction_subset
                    ORDER BY volume, build_id
                ) AS rn
        FROM exploded
    )
    INSERT INTO public.smallest_door_records
           (id, orientation, door_width, door_height, door_depth,
            types, restrictions, volume, restriction_subset)
    SELECT build_id, orientation, door_width, door_height,
           door_depth, types, restrictions, volume, restriction_subset
    FROM   ranked
    WHERE  rn = 1;
END;
$$;


--
-- Name: refresh_smallest_after_door_delete(bigint); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.refresh_smallest_after_door_delete(IN p_build_id bigint)
    LANGUAGE sql
    AS $$
--------------------------------------------------------------------
--  A.  All (orientation,dims,types,subset) combos where the *old*
--      build was the record-holder.
--------------------------------------------------------------------
WITH affected AS (
    SELECT orientation,
           door_width,
           door_height,
           door_depth,
           types,
           restriction_subset
    FROM   public.smallest_door_records
    WHERE  id = p_build_id
),

--------------------------------------------------------------------
--  B.  Remove those (now stale) rows in one shot.
--------------------------------------------------------------------
del AS (
    DELETE FROM public.smallest_door_records s
    USING affected a
    WHERE s.orientation        = a.orientation
      AND s.door_width         = a.door_width
      AND s.door_height        = a.door_height
      AND s.door_depth         = a.door_depth
      AND s.types              = a.types
      AND s.restriction_subset = a.restriction_subset
    RETURNING a.*                                   -- feed step C
),

--------------------------------------------------------------------
--  C.  Re-compute the winners for every combo we just deleted,
--      but using *all remaining* builds (p_build_id is gone).
--------------------------------------------------------------------
base AS (
    SELECT
        b.id                                            AS build_id,
        d.orientation,
        d.door_width,
        d.door_height,
        COALESCE(d.door_depth, 1)                       AS door_depth,
        COALESCE(
            ARRAY_AGG(DISTINCT t.name ORDER BY t.name)
                FILTER (WHERE t.name IS NOT NULL),
            ARRAY[]::text[]
        ) AS types,
        COALESCE(
            ARRAY_AGG(DISTINCT r.name ORDER BY r.name)
                FILTER (WHERE r.name IS NOT NULL),
            ARRAY[]::text[]
        ) AS restrictions,
        b.width * b.height * b.depth AS volume
    FROM   public.builds             b
    JOIN   public.doors              d  ON d.build_id = b.id
    LEFT   JOIN public.build_types   bt ON bt.build_id = b.id
    LEFT   JOIN public.types         t  ON t.id = bt.type_id
    LEFT   JOIN public.build_restrictions br ON br.build_id = b.id
    LEFT   JOIN public.restrictions  r  ON r.id = br.restriction_id
    WHERE  b.submission_status = 1
      AND  b.category          = 'Door'
      AND  b.width IS NOT NULL
      AND  b.height IS NOT NULL
      AND  b.depth IS NOT NULL
      AND  b.id <> p_build_id                         -- <-- removed build
    GROUP  BY b.id, d.orientation, d.door_width,
              d.door_height, d.door_depth
),
candidates AS (
    SELECT b.*, d.restriction_subset
    FROM   base b
    JOIN   del  d
      ON   b.orientation = d.orientation
     AND   b.door_width  = d.door_width
     AND   b.door_height = d.door_height
     AND   b.door_depth  = d.door_depth
     AND   b.types       = d.types
    WHERE  d.restriction_subset <@ b.restrictions      -- subset test
),
ranked AS (
    SELECT DISTINCT ON
           (orientation, door_width, door_height,
            door_depth, types, restriction_subset)
           build_id        AS id,
           orientation, door_width, door_height,
           door_depth, types, restrictions,
           volume, restriction_subset
    FROM   candidates
    ORDER  BY orientation, door_width, door_height,
             door_depth, types, restriction_subset,
             volume, id
)

--------------------------------------------------------------------
--  D.  Insert the new winners (if any).
--------------------------------------------------------------------
INSERT INTO public.smallest_door_records
       (id, orientation, door_width, door_height, door_depth,
        types, restrictions, volume, restriction_subset)
SELECT * FROM ranked;
$$;


--
-- Name: refresh_smallest_for_door_insert(bigint); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.refresh_smallest_for_door_insert(IN p_build_id bigint)
    LANGUAGE sql
    AS $$
WITH b AS (                               -- the changed build only
    SELECT
        b.id   AS build_id,
        d.orientation,
        d.door_width,
        d.door_height,
        COALESCE(d.door_depth, 1)               AS door_depth,
        COALESCE(
            ARRAY_AGG(DISTINCT t.name ORDER BY t.name)
                FILTER (WHERE t.name IS NOT NULL),
            ARRAY[]::text[]
        ) AS types,
        COALESCE(
            ARRAY_AGG(DISTINCT r.name ORDER BY r.name)
                FILTER (WHERE r.name IS NOT NULL),
            ARRAY[]::text[]
        ) AS restrictions,
        b.width * b.height * b.depth AS volume
    FROM   public.builds             b
    JOIN   public.doors              d  ON d.build_id = b.id
    LEFT   JOIN public.build_types   bt ON bt.build_id = b.id
    LEFT   JOIN public.types         t  ON t.id = bt.type_id
    LEFT   JOIN public.build_restrictions br ON br.build_id = b.id
    LEFT   JOIN public.restrictions  r  ON r.id = br.restriction_id
    WHERE  b.id = p_build_id
        AND  b.submission_status = 1
        AND  b.category          = 'Door'
        AND  b.width IS NOT NULL
        AND  b.height IS NOT NULL
        AND  b.depth IS NOT NULL
    GROUP  BY b.id, d.orientation, d.door_width,
              d.door_height, d.door_depth
), subset AS (
    SELECT
        b.build_id, b.orientation, b.door_width,
        b.door_height, b.door_depth,
        b.types, b.restrictions,
        ps AS restriction_subset, b.volume
    FROM   b, LATERAL power_set_max(b.restrictions, 8) ps
), ranked AS (            -- winner per (dims, types, subset)
    SELECT DISTINCT ON
           (orientation, door_width, door_height,
            door_depth, types, restriction_subset)
           build_id            AS id,
           orientation, door_width, door_height,
           door_depth, types, restrictions,
           volume, restriction_subset
    FROM   subset
    ORDER  BY orientation, door_width, door_height, door_depth,
             types, restriction_subset,
             volume, id
)
INSERT INTO public.smallest_door_records AS s
       (id, orientation, door_width, door_height, door_depth,
        types, restrictions, volume, restriction_subset)
SELECT * FROM ranked
ON CONFLICT (orientation, door_width, door_height,
             door_depth, types, restriction_subset)
DO UPDATE
    SET id            = EXCLUDED.id,
        restrictions  = EXCLUDED.restrictions,
        volume        = EXCLUDED.volume
    WHERE s.volume > EXCLUDED.volume;   -- update only if we really won
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


--
-- Name: sync_new_restriction(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sync_new_restriction() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    -- string we are searching for
    b_restriction        text;

    -- id that will go into build_restrictions
    b_restriction_id     int;

    -- category / type of the *restriction*
    r_category           text;
    r_type               text;

    -- json key inside unknown_restrictions matching r_type
    json_key             text;
BEGIN
    -- 1. figure out category & type (alias rows don’t have them)
    IF TG_TABLE_NAME = 'restrictions' THEN
        b_restriction    := NEW.name;
        b_restriction_id := NEW.id;
        r_category       := NEW.build_category;
        r_type           := NEW.type;
    ELSIF TG_TABLE_NAME = 'restriction_aliases' THEN
        b_restriction    := NEW.alias;
        b_restriction_id := NEW.restriction_id;

        SELECT r.build_category, r.type
        INTO   r_category, r_type
        FROM   restrictions r
        WHERE  r.id = NEW.restriction_id;
    ELSE
        RAISE EXCEPTION 'sync_new_restriction() fired by unexpected table %', TG_TABLE_NAME;
    END IF;

    -- 2. map type → correct json key
    json_key := CASE r_type
                  WHEN 'component'        THEN 'component_restrictions'
                  WHEN 'wiring-placement' THEN 'wiring_placement_restrictions'
                  WHEN 'miscellaneous'    THEN 'miscellaneous_restrictions'
                END;

    IF json_key IS NULL THEN
        RAISE NOTICE 'Restriction type % is unsupported – skipped', r_type;
        RETURN NULL;
    END IF;

    -- 3. touch only builds with same category & containing the string
    WITH affected AS (
        SELECT b.id,
               (
                   WITH elems AS (
                       SELECT jsonb_array_elements_text(
                                  b.extra_info -> 'unknown_restrictions' -> json_key
                              ) AS val
                   ),
                   kept AS (
                       SELECT jsonb_agg(to_jsonb(val)) AS arr
                       FROM   elems
                       WHERE  lower(val) <> lower(b_restriction)
                   )
                   SELECT CASE
                              WHEN (SELECT arr FROM kept) IS NULL THEN
                                  CASE
                                      WHEN ((b.extra_info -> 'unknown_restrictions') - json_key) = '{}'::jsonb THEN
                                          b.extra_info - 'unknown_restrictions'
                                      ELSE
                                          jsonb_set(
                                              b.extra_info,
                                              '{unknown_restrictions}',
                                              (b.extra_info -> 'unknown_restrictions') - json_key,
                                              TRUE
                                          )
                                  END
                              ELSE
                                  jsonb_set(
                                      b.extra_info,
                                      ARRAY['unknown_restrictions', json_key],
                                      (SELECT arr FROM kept),
                                      TRUE
                                  )
                          END
               ) AS new_extra
        FROM   builds b
        WHERE  b.category = r_category
          AND  EXISTS (
              SELECT 1
              FROM   jsonb_array_elements_text(
                         b.extra_info -> 'unknown_restrictions' -> json_key
                     ) AS t(val)
              WHERE  lower(val) = lower(b_restriction)
          )
    ),
    changed AS (
        UPDATE builds b
        SET    extra_info = a.new_extra
        FROM   affected a
        WHERE  b.id = a.id
        RETURNING b.id
    )
    -- 4. link builds to the new restriction (ignore dupes)
    INSERT INTO build_restrictions (build_id, restriction_id)
    SELECT id, b_restriction_id
    FROM   changed
    ON CONFLICT DO NOTHING;

    RETURN NULL;  -- AFTER trigger
END;
$$;


--
-- Name: trg_refresh_smallest_door(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_refresh_smallest_door() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        CALL public.refresh_smallest_after_door_delete(OLD.build_id);

    ELSIF TG_OP = 'INSERT' THEN
        -- remove the stale rows for *this* build first
        -- The reason why we need to delete the old winners even for INSERT is that
        -- here, INSERT can also mean "insert a new type/restriction" for an existing door,
        CALL public.refresh_smallest_after_door_delete(NEW.build_id);
        CALL public.refresh_smallest_for_door_insert(NEW.build_id);

    ELSE -- UPDATE
        -- First remove the “old” winners, then add the “new” ones
        CALL public.refresh_smallest_after_door_delete(OLD.build_id);
        CALL public.refresh_smallest_for_door_insert(NEW.build_id);

    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: trg_refresh_smallest_door_from_builds(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_refresh_smallest_door_from_builds() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        CALL public.refresh_smallest_after_door_delete(OLD.id);
    ELSE                               -- INSERT or UPDATE
        CALL public.refresh_smallest_after_door_delete(OLD.id);
        CALL public.refresh_smallest_for_door_insert(NEW.id);
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: build_creators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_creators (
    build_id bigint NOT NULL,
    user_id integer NOT NULL
);


--
-- Name: TABLE build_creators; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_creators IS 'Association table between builds and their creators.';


--
-- Name: build_edit_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_edit_history (
    build_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    version smallint NOT NULL
);


--
-- Name: TABLE build_edit_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_edit_history IS 'A version marker recorded when a build is edited.';


--
-- Name: build_edit_history_build_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.build_edit_history ALTER COLUMN build_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.build_edit_history_build_id_seq
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

COMMENT ON TABLE public.build_links IS 'A link associated with a build (image, video, world download).';


--
-- Name: build_restrictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_restrictions (
    build_id bigint NOT NULL,
    restriction_id smallint NOT NULL
);


--
-- Name: TABLE build_restrictions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_restrictions IS 'Association table between builds and their restrictions.';


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
    created_by_discord_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
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
-- Name: build_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_types (
    build_id bigint NOT NULL,
    type_id smallint NOT NULL
);


--
-- Name: TABLE build_types; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.build_types IS 'Association table between builds and their types.';


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
-- Name: build_vote_sessions_session_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.build_vote_sessions ALTER COLUMN vote_session_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.build_vote_sessions_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


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
-- Name: delete_log_vote_sessions_vote_session_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.delete_log_vote_sessions ALTER COLUMN vote_session_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.delete_log_vote_sessions_vote_session_id_seq
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
-- Name: entrances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entrances (
    build_id bigint NOT NULL
);


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
    CONSTRAINT record_definitions_materialization_source_check CHECK ((materialization_source = ANY (ARRAY['eager'::text, 'seeded'::text, 'public_lookup'::text]))),
    CONSTRAINT record_definitions_record_class_check CHECK ((record_class = ANY (ARRAY['first'::text, 'fastest'::text, 'smallest'::text, 'fastest_smallest'::text, 'smallest_fastest'::text]))),
    CONSTRAINT record_definitions_version_scope_check CHECK ((version_scope = ANY (ARRAY['all_time'::text, 'current'::text])))
);


--
-- Name: TABLE record_definitions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_definitions IS 'A stable identity for one record competition.';


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
    last_error text
);


--
-- Name: TABLE record_recompute_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.record_recompute_queue IS 'A durable request to recompute an affected record scope.';


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
-- Name: restriction_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.restriction_aliases (
    restriction_id smallint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    alias text NOT NULL
);


--
-- Name: TABLE restriction_aliases; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.restriction_aliases IS 'An alias for a restriction, allowing for alternative names.';


--
-- Name: restriction_aliases_restriction_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.restriction_aliases ALTER COLUMN restriction_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.restriction_aliases_restriction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: restrictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.restrictions (
    id smallint NOT NULL,
    build_category text,
    name text,
    type text
);


--
-- Name: TABLE restrictions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.restrictions IS 'A restriction that can be applied to builds.';


--
-- Name: restrictions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.restrictions_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: restrictions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.restrictions_id_seq OWNED BY public.restrictions.id;


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
    last_error text
);


--
-- Name: TABLE search_embedding_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_embedding_queue IS 'A durable request to embed a search document whose source hash changed.';


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
    CONSTRAINT search_projection_queue_action_check CHECK ((action = ANY (ARRAY['upsert'::text, 'delete'::text])))
);


--
-- Name: TABLE search_projection_queue; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_projection_queue IS 'A durable request to refresh or delete a projected search resource.';


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
    trusted_roles_ids bigint[],
    staff_roles_ids bigint[]
);


--
-- Name: TABLE server_settings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.server_settings IS 'Settings for a Discord server.';


--
-- Name: smallest_door_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.smallest_door_records (
    record_id bigint NOT NULL,
    id bigint NOT NULL,
    title text,
    orientation text NOT NULL,
    door_width integer NOT NULL,
    door_height integer NOT NULL,
    door_depth integer DEFAULT 1 NOT NULL,
    types text[] NOT NULL,
    restrictions text[] DEFAULT '{}'::text[] NOT NULL,
    volume integer NOT NULL,
    restriction_subset text[] NOT NULL
);


--
-- Name: TABLE smallest_door_records; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.smallest_door_records IS 'A door that is the smallest in a specific category.

This table is a cache for the smallest doors in each category up to 8 restrictions, built by using database triggers';


--
-- Name: smallest_door_records_record_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.smallest_door_records_record_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: smallest_door_records_record_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.smallest_door_records_record_id_seq OWNED BY public.smallest_door_records.record_id;


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
    numeric_quantum numeric,
    render_template text NOT NULL,
    default_display_order smallint NOT NULL,
    moderation_status text NOT NULL,
    created_by_discord_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    CONSTRAINT tag_definitions_authority_check CHECK ((authority = ANY (ARRAY['official'::text, 'user'::text]))),
    CONSTRAINT tag_definitions_display_order_check CHECK ((default_display_order >= 0)),
    CONSTRAINT tag_definitions_moderation_status_check CHECK ((moderation_status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'archived'::text]))),
    CONSTRAINT tag_definitions_non_numeric_unit_check CHECK (((value_type = 'numeric'::text) OR ((canonical_unit_key IS NULL) AND (default_display_unit_key IS NULL) AND (numeric_quantum IS NULL)))),
    CONSTRAINT tag_definitions_numeric_metadata_check CHECK ((((value_type = 'numeric'::text) = ((canonical_unit_key IS NOT NULL) OR (numeric_quantum IS NOT NULL))) OR ((value_type = 'numeric'::text) AND (canonical_unit_key IS NULL) AND (numeric_quantum IS NULL)))),
    CONSTRAINT tag_definitions_numeric_quantum_check CHECK (((numeric_quantum IS NULL) OR (numeric_quantum > (0)::numeric))),
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
-- Name: types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.types (
    id smallint NOT NULL,
    build_category text,
    name text
);


--
-- Name: TABLE types; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.types IS 'A build pattern.';


--
-- Name: types_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.types_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.types_id_seq OWNED BY public.types.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    discord_id bigint,
    minecraft_uuid uuid,
    ign text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.users IS 'A user in the system, which can be linked to both Discord and Minecraft accounts.';


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: utilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.utilities (
    build_id bigint NOT NULL
);


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
    valid boolean DEFAULT true NOT NULL
);


--
-- Name: TABLE verification_codes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.verification_codes IS 'A verification code for linking Minecraft accounts.';


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
    patch_number smallint NOT NULL
);


--
-- Name: TABLE versions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.versions IS 'A version of Minecraft that a build is compatible with.';


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
    CONSTRAINT vote_session_options_choice_check CHECK ((choice = ANY (ARRAY['approve'::text, 'deny'::text]))),
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
    author_id bigint NOT NULL,
    kind text NOT NULL,
    fail_threshold smallint NOT NULL,
    pass_threshold smallint NOT NULL,
    result text DEFAULT 'pending'::text NOT NULL,
    CONSTRAINT vote_sessions_fail_threshold_check CHECK ((fail_threshold < 0)),
    CONSTRAINT vote_sessions_pass_threshold_check CHECK ((pass_threshold > 0)),
    CONSTRAINT vote_sessions_result_check CHECK ((result = ANY (ARRAY['approved'::text, 'denied'::text, 'cancelled'::text, 'pending'::text])))
);


--
-- Name: TABLE vote_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.vote_sessions IS 'A voting session for builds or log deletions.';


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
    user_id bigint NOT NULL,
    weight double precision
);


--
-- Name: TABLE votes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.votes IS 'A vote cast in a vote session.';


--
-- Name: votes_vote_session_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.votes ALTER COLUMN vote_session_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.votes_vote_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: builds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.builds ALTER COLUMN id SET DEFAULT nextval('public.submissions_submission_id_seq'::regclass);


--
-- Name: restrictions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restrictions ALTER COLUMN id SET DEFAULT nextval('public.restrictions_id_seq'::regclass);


--
-- Name: smallest_door_records record_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smallest_door_records ALTER COLUMN record_id SET DEFAULT nextval('public.smallest_door_records_record_id_seq'::regclass);


--
-- Name: types id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.types ALTER COLUMN id SET DEFAULT nextval('public.types_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: verification_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verification_codes ALTER COLUMN id SET DEFAULT nextval('public.verification_codes_id_seq'::regclass);


--
-- Name: versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.versions ALTER COLUMN id SET DEFAULT nextval('public.versions_id_seq'::regclass);


--
-- Name: build_creators build_creators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_pkey PRIMARY KEY (build_id, user_id);


--
-- Name: build_edit_history build_edit_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_edit_history_pkey PRIMARY KEY (build_id);


--
-- Name: build_links build_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_links
    ADD CONSTRAINT build_links_pkey PRIMARY KEY (build_id, url);


--
-- Name: build_restrictions build_restrictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_restrictions
    ADD CONSTRAINT build_restrictions_pkey PRIMARY KEY (build_id, restriction_id);


--
-- Name: build_tag_assignments build_tag_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_pkey PRIMARY KEY (build_id, tag_id);


--
-- Name: build_types build_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_types
    ADD CONSTRAINT build_types_pkey PRIMARY KEY (build_id, type_id);


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
-- Name: delete_log_vote_sessions delete_log_vote_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.delete_log_vote_sessions
    ADD CONSTRAINT delete_log_vote_sessions_pkey PRIMARY KEY (vote_session_id);


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
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


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
-- Name: restriction_aliases restriction_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restriction_aliases
    ADD CONSTRAINT restriction_aliases_pkey PRIMARY KEY (alias);


--
-- Name: restrictions restrictions_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restrictions
    ADD CONSTRAINT restrictions_name_key UNIQUE (name);


--
-- Name: restrictions restrictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restrictions
    ADD CONSTRAINT restrictions_pkey PRIMARY KEY (id);


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
-- Name: smallest_door_records smallest_door_records_orientation_door_width_door_height_do_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smallest_door_records
    ADD CONSTRAINT smallest_door_records_orientation_door_width_door_height_do_key UNIQUE (orientation, door_width, door_height, door_depth, types, restriction_subset);


--
-- Name: smallest_door_records smallest_door_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smallest_door_records
    ADD CONSTRAINT smallest_door_records_pkey PRIMARY KEY (record_id);


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
-- Name: types types_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.types
    ADD CONSTRAINT types_name_key UNIQUE (name);


--
-- Name: types types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.types
    ADD CONSTRAINT types_pkey PRIMARY KEY (id);


--
-- Name: build_edit_history unique_version_per_build; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT unique_version_per_build UNIQUE (build_id, version);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: utilities utilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utilities
    ADD CONSTRAINT utilities_pkey PRIMARY KEY (build_id);


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
    ADD CONSTRAINT vote_session_options_pkey PRIMARY KEY (vote_session_id, emoji);


--
-- Name: vote_session_options vote_session_options_vote_session_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_session_options
    ADD CONSTRAINT vote_session_options_vote_session_id_position_key UNIQUE (vote_session_id, "position");


--
-- Name: vote_sessions vote_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vote_sessions
    ADD CONSTRAINT vote_sessions_pkey PRIMARY KEY (id);


--
-- Name: votes votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_pkey PRIMARY KEY (vote_session_id, user_id);


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
-- Name: idx_smallest_door_records_dims; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_smallest_door_records_dims ON public.smallest_door_records USING btree (orientation, door_width, door_height, door_depth);


--
-- Name: idx_smallest_door_records_restrictions_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_smallest_door_records_restrictions_gin ON public.smallest_door_records USING gin (restrictions);


--
-- Name: idx_smallest_door_records_types_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_smallest_door_records_types_gin ON public.smallest_door_records USING gin (types);


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
-- Name: record_holder_history_definition_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_holder_history_definition_idx ON public.record_holder_history USING btree (definition_id, held_from);


--
-- Name: record_recompute_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_recompute_queue_ready_idx ON public.record_recompute_queue USING btree (enqueued_at) WHERE (locked_at IS NULL);


--
-- Name: record_result_holders_build_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_result_holders_build_idx ON public.record_result_holders USING btree (build_id);


--
-- Name: record_results_definition_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_results_definition_idx ON public.record_results USING btree (definition_id);


--
-- Name: restriction_aliases_restriction_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX restriction_aliases_restriction_id_idx ON public.restriction_aliases USING btree (restriction_id);


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

CREATE INDEX search_embedding_queue_ready_idx ON public.search_embedding_queue USING btree (enqueued_at) WHERE (locked_at IS NULL);


--
-- Name: search_projection_queue_ready_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX search_projection_queue_ready_idx ON public.search_projection_queue USING btree (enqueued_at) WHERE (locked_at IS NULL);


--
-- Name: tag_aliases_normalized_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tag_aliases_normalized_idx ON public.tag_aliases USING btree (normalized_alias);


--
-- Name: tag_definitions_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tag_definitions_lookup_idx ON public.tag_definitions USING btree (normalized_name, semantic_kind);


--
-- Name: unq_smallest_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX unq_smallest_key ON public.smallest_door_records USING btree (orientation, door_width, door_height, door_depth, types, restriction_subset);


--
-- Name: build_creators build_creators_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_creators_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_restrictions build_restrictions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_restrictions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_restrictions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_restrictions build_restrictions_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_restrictions_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.build_restrictions FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();


--
-- Name: build_tag_assignments build_tag_assignments_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_tag_assignments_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_types build_types_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_types_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_types FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: build_types build_types_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_types_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.build_types FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();


--
-- Name: build_versions build_versions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: builds builds_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: builds builds_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door_from_builds();


--
-- Name: builds delete_orphaned_build_vote_sessions_after_builds; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds AFTER DELETE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete();


--
-- Name: door_timing_variants door_timing_variants_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER door_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: doors doors_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: doors doors_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();


--
-- Name: extender_timing_variants extender_timing_variants_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extender_timing_variants_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extender_timing_variants FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


--
-- Name: extenders extenders_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER extenders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();


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
-- Name: restriction_aliases restriction_aliases_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER restriction_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.restriction_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: restrictions restrictions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER restrictions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.restrictions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: builds set_locked_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_locked_at BEFORE UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.set_locked_at();


--
-- Name: smallest_door_records smallest_door_records_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER smallest_door_records_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.smallest_door_records FOR EACH ROW EXECUTE FUNCTION public.enqueue_legacy_record_search_projection();


--
-- Name: restrictions trg_sync_on_tag; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sync_on_tag AFTER INSERT ON public.restrictions FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction();


--
-- Name: restriction_aliases trg_sync_on_tag_alias; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sync_on_tag_alias AFTER INSERT ON public.restriction_aliases FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction();


--
-- Name: types types_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER types_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.types FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: messages update_messages_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_messages_updated_at BEFORE UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: users users_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER users_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: versions versions_enqueue_search; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();


--
-- Name: build_creators build_creators_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_creators build_creators_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_creators
    ADD CONSTRAINT build_creators_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: build_edit_history build_edit_history_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_edit_history
    ADD CONSTRAINT build_edit_history_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: build_links build_links_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_links
    ADD CONSTRAINT build_links_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_restrictions build_restrictions_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_restrictions
    ADD CONSTRAINT build_restrictions_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_restrictions build_restrictions_restriction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_restrictions
    ADD CONSTRAINT build_restrictions_restriction_id_fkey FOREIGN KEY (restriction_id) REFERENCES public.restrictions(id) ON DELETE RESTRICT;


--
-- Name: build_tag_assignments build_tag_assignments_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


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
-- Name: build_types build_types_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_types
    ADD CONSTRAINT build_types_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: build_types build_types_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.build_types
    ADD CONSTRAINT build_types_type_id_fkey FOREIGN KEY (type_id) REFERENCES public.types(id) ON DELETE RESTRICT;


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
-- Name: builds builds_original_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.builds
    ADD CONSTRAINT builds_original_message_id_fkey FOREIGN KEY (original_message_id) REFERENCES public.messages(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: delete_log_vote_sessions delete_log_vote_sessions_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.delete_log_vote_sessions
    ADD CONSTRAINT delete_log_vote_sessions_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


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
-- Name: messages messages_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON DELETE SET NULL;


--
-- Name: messages public_messages_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT public_messages_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: messages public_messages_server_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT public_messages_server_id_fkey FOREIGN KEY (server_id) REFERENCES public.server_settings(server_id) ON DELETE RESTRICT;


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
-- Name: restriction_aliases restriction_aliases_restriction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restriction_aliases
    ADD CONSTRAINT restriction_aliases_restriction_id_fkey FOREIGN KEY (restriction_id) REFERENCES public.restrictions(id) ON UPDATE CASCADE ON DELETE CASCADE;


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
-- Name: smallest_door_records smallest_door_records_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smallest_door_records
    ADD CONSTRAINT smallest_door_records_id_fkey FOREIGN KEY (id) REFERENCES public.builds(id) ON DELETE CASCADE;


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
-- Name: votes votes_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

