-- Portable application schema baseline.
-- Generated from the remote public schema on 2026-07-28, then advanced through the pending repository migrations.
-- Supabase-only grants, policies, and row-level security are intentionally excluded.

CREATE EXTENSION IF NOT EXISTS vector;

--
-- PostgreSQL database dump
--

-- Dumped from database version 17.4
-- Dumped by pg_dump version 17.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


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
    edited_time timestamp with time zone DEFAULT (now() AT TIME ZONE 'utc'::text),
    record_category text,
    extra_info jsonb DEFAULT '{}'::jsonb NOT NULL,
    width integer,
    height integer,
    depth integer,
    completion_time text,
    submission_time timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    category text,
    submitter_id bigint NOT NULL,
    ai_generated boolean NOT NULL,
    original_message_id bigint,
    version_spec text,
    embedding public.vector(1536),
    is_locked boolean DEFAULT false NOT NULL,
    locked_at timestamp with time zone,
    CONSTRAINT check_record_category CHECK ((record_category = ANY (ARRAY['Smallest'::text, 'Fastest'::text, 'First'::text, 'Smallest Fastest'::text, 'Fastest Smallest'::text, NULL::text]))),
    CONSTRAINT check_status CHECK ((submission_status = ANY (ARRAY[0, 1, 2]))),
    CONSTRAINT submissions_build_depth_check CHECK ((depth > 0)),
    CONSTRAINT submissions_build_height_check CHECK ((height > 0)),
    CONSTRAINT submissions_build_width_check CHECK ((width > 0))
);


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
-- Name: build_edit_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_edit_history (
    build_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    version smallint NOT NULL
);


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
-- Name: build_restrictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_restrictions (
    build_id bigint NOT NULL,
    restriction_id smallint NOT NULL
);


--
-- Name: build_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_types (
    build_id bigint NOT NULL,
    type_id smallint NOT NULL
);


--
-- Name: build_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.build_versions (
    build_id bigint NOT NULL,
    version_id smallint NOT NULL
);


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
-- Name: extenders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.extenders (
    build_id bigint NOT NULL
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
-- Name: types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.types (
    id smallint NOT NULL,
    build_category text,
    name text
);


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
    created_at timestamp without time zone DEFAULT now()
);


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
    created timestamp without time zone DEFAULT now() NOT NULL,
    expires timestamp without time zone DEFAULT (now() + '00:10:00'::interval) NOT NULL,
    username text NOT NULL,
    valid boolean DEFAULT true NOT NULL
);


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
-- Name: restriction_aliases_restriction_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX restriction_aliases_restriction_id_idx ON public.restriction_aliases USING btree (restriction_id);


--
-- Name: unq_smallest_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX unq_smallest_key ON public.smallest_door_records USING btree (orientation, door_width, door_height, door_depth, types, restriction_subset);


--
-- Name: build_restrictions build_restrictions_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_restrictions_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.build_restrictions FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();


--
-- Name: build_types build_types_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER build_types_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.build_types FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();


--
-- Name: builds builds_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER builds_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door_from_builds();


--
-- Name: builds delete_orphaned_build_vote_sessions_after_builds; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds AFTER DELETE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete();


--
-- Name: doors doors_refresh_smallest_door; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doors_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();


--
-- Name: builds set_locked_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_locked_at BEFORE UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.set_locked_at();


--
-- Name: restrictions trg_sync_on_tag; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sync_on_tag AFTER INSERT ON public.restrictions FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction();


--
-- Name: restriction_aliases trg_sync_on_tag_alias; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sync_on_tag_alias AFTER INSERT ON public.restriction_aliases FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction();


--
-- Name: messages update_messages_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_messages_updated_at BEFORE UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


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
-- Name: restriction_aliases restriction_aliases_restriction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restriction_aliases
    ADD CONSTRAINT restriction_aliases_restriction_id_fkey FOREIGN KEY (restriction_id) REFERENCES public.restrictions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: smallest_door_records smallest_door_records_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smallest_door_records
    ADD CONSTRAINT smallest_door_records_id_fkey FOREIGN KEY (id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: utilities utilities_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utilities
    ADD CONSTRAINT utilities_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;


--
-- Name: votes votes_vote_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: build_creators Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: build_edit_history Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: build_links Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: build_restrictions Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: build_types Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: build_versions Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: builds Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: doors Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: entrances Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: extenders Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: restriction_aliases Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: restrictions Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: smallest_door_records Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: types Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: users Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: utilities Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: versions Enable read access for all users; Type: POLICY; Schema: public; Owner: -
--



--
-- Name: build_creators; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: build_edit_history; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: build_links; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: build_restrictions; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: build_types; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: build_versions; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: build_vote_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: builds; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: delete_log_vote_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: doors; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: entrances; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: extenders; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: messages; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: restriction_aliases; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: restrictions; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: server_settings; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: smallest_door_records; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: types; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: utilities; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: verification_codes; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: versions; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: vote_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- Name: votes; Type: ROW SECURITY; Schema: public; Owner: -
--


--
-- PostgreSQL database dump complete
--

SET search_path = public, pg_catalog;




