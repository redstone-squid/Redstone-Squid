-- https://chatgpt.com/c/684d7271-3858-8002-a8a9-185933336574
CREATE MATERIALIZED VIEW public.door_search_cache AS
SELECT
    b.id                  AS build_id,
    d.orientation,
    d.door_width,
    d.door_height,
    COALESCE(d.door_depth, 1) AS door_depth,

    /* types: must be present; no COALESCE, no default */
    COALESCE(
        ARRAY_AGG(DISTINCT t.name ORDER BY t.name)
            FILTER (WHERE t.name IS NOT NULL),
        ARRAY []::text[]
    ) AS types,

    /* restrictions: allow empty, but never [NULL] */
    COALESCE(
        ARRAY_AGG(DISTINCT r.name ORDER BY r.name)
            FILTER (WHERE r.name IS NOT NULL),
        ARRAY[]::text[]
    ) AS restrictions

FROM   public.builds            b
LEFT   JOIN public.doors        d  ON d.build_id = b.id
LEFT   JOIN public.build_types  bt ON bt.build_id = b.id
LEFT   JOIN public.types        t  ON t.id = bt.type_id
LEFT   JOIN public.build_restrictions br ON br.build_id = b.id
LEFT   JOIN public.restrictions r  ON r.id = br.restriction_id
WHERE b.category = 'Door'
GROUP  BY b.id, d.orientation, d.door_width, d.door_height, d.door_depth;

-- Composite B-tree for exact‐match or range filters on the structural dimensions
CREATE INDEX idx_bsc_dimensions
    ON public.door_search_cache
        (orientation, door_width, door_height, door_depth);

-- GIN index for set/containment search on arrays
CREATE INDEX idx_bsc_types_gin
    ON public.door_search_cache
        USING GIN (types);

CREATE INDEX idx_bsc_restrictions_gin
    ON public.door_search_cache
        USING GIN (restrictions);


-- Returns every subset whose cardinality ≤ max_k (default 8).
-- Uses bit-mask enumeration but skips masks with > max_k bits set,
-- so we never materialise the huge supersets
CREATE OR REPLACE FUNCTION public.power_set_max(
        txt      text[],
        max_k    int DEFAULT 8
) RETURNS SETOF text[]
  LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS
$$
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

CREATE TABLE public.smallest_door_records (
    id                int NOT NULL REFERENCES public.builds (id) ON DELETE CASCADE,
    -- title of the build, cannot be populated easily by the db itself because it is a computed field
    -- so we leave it as NULL for now, and possibly run a cron job to fill it in later
    title             text DEFAULT NULL,
    orientation       text NOT NULL,
    door_width        int NOT NULL,
    door_height       int NOT NULL,
    door_depth        int NOT NULL DEFAULT 1,
    types             text[] NOT NULL, -- must be present
    restrictions      text[] NOT NULL DEFAULT '{}', -- can be empty
    volume            bigint NOT NULL, -- pre-computed volume
    restriction_subset text[] NOT NULL, -- subset of restrictions
    PRIMARY KEY (orientation, door_width, door_height, door_depth, types, restriction_subset)
);

CREATE OR REPLACE PROCEDURE public.rebuild_smallest_door_records()
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
                ARRAY []::text[]
            ) AS types,
            COALESCE(
                ARRAY_AGG(DISTINCT r.name ORDER BY r.name)
                    FILTER (WHERE r.name IS NOT NULL),
                ARRAY[]::text[]
            ) AS restrictions,
            d.door_width * d.door_height * COALESCE(d.door_depth, 1) AS volume
        FROM   public.builds             b
        JOIN   public.doors              d  ON d.build_id = b.id
        LEFT   JOIN public.build_types   bt ON bt.build_id = b.id
        LEFT   JOIN public.types         t  ON t.id = bt.type_id
        LEFT   JOIN public.build_restrictions br ON br.build_id = b.id
        LEFT   JOIN public.restrictions  r  ON r.id = br.restriction_id
        WHERE  b.submission_status = 1
          AND  b.category = 'Door'
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

CREATE UNIQUE INDEX unq_smallest_key
      ON public.smallest_door_records
          (orientation, door_width, door_height,
           door_depth, types, restriction_subset);

CREATE INDEX idx_smallest_door_records_dims
      ON public.smallest_door_records
          (orientation, door_width, door_height, door_depth); -- optional now

CREATE INDEX idx_smallest_door_records_types_gin
      ON public.smallest_door_records
      USING GIN (types);

CREATE INDEX idx_smallest_door_records_restrictions_gin
      ON public.smallest_door_records
      USING GIN (restrictions);

-- Populate the smallest_door_records table once at the start
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.smallest_door_records) THEN
        CALL public.rebuild_smallest_door_records();
    END IF;
END $$;


CREATE OR REPLACE PROCEDURE public.refresh_smallest_after_door_delete(p_build_id bigint)
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
            ARRAY []::text[]
        ) AS types,
        COALESCE(
            ARRAY_AGG(DISTINCT r.name ORDER BY r.name)
                FILTER (WHERE r.name IS NOT NULL),
            ARRAY[]::text[]
        ) AS restrictions,
        d.door_width * d.door_height *
        COALESCE(d.door_depth, 1)                       AS volume
    FROM   public.builds             b
    JOIN   public.doors              d  ON d.build_id = b.id
    LEFT   JOIN public.build_types   bt ON bt.build_id = b.id
    LEFT   JOIN public.types         t  ON t.id = bt.type_id
    LEFT   JOIN public.build_restrictions br ON br.build_id = b.id
    LEFT   JOIN public.restrictions  r  ON r.id = br.restriction_id
    WHERE  b.submission_status = 1
      AND  b.category          = 'Door'
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


CREATE OR REPLACE PROCEDURE public.refresh_smallest_for_door_insert(p_build_id bigint)
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
            ARRAY []::text[]
        ) AS types,
        COALESCE(
            ARRAY_AGG(DISTINCT r.name ORDER BY r.name)
                FILTER (WHERE r.name IS NOT NULL),
            ARRAY[]::text[]
        ) AS restrictions,
        d.door_width * d.door_height *
        COALESCE(d.door_depth, 1)               AS volume
    FROM   public.builds             b
    JOIN   public.doors              d  ON d.build_id = b.id
    LEFT   JOIN public.build_types   bt ON bt.build_id = b.id
    LEFT   JOIN public.types         t  ON t.id = bt.type_id
    LEFT   JOIN public.build_restrictions br ON br.build_id = b.id
    LEFT   JOIN public.restrictions  r  ON r.id = br.restriction_id
    WHERE  b.id = p_build_id
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


CREATE OR REPLACE FUNCTION public.trg_refresh_smallest_door()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        CALL public.refresh_smallest_after_door_delete(OLD.build_id);

    ELSIF TG_OP = 'UPDATE' THEN
        -- First remove the “old” winners, then add the “new” ones
        CALL public.refresh_smallest_after_door_delete(OLD.build_id);
        CALL public.refresh_smallest_for_door_insert(NEW.build_id);

    ELSE   -- INSERT
        CALL public.refresh_smallest_for_door_insert(NEW.build_id);
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER doors_refresh_smallest
AFTER INSERT OR UPDATE OR DELETE ON public.doors
FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();

CREATE TRIGGER build_types_refresh_door_smallest
AFTER INSERT OR UPDATE OR DELETE ON public.build_types
FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();

CREATE TRIGGER build_restrictions_refresh_door_smallest
AFTER INSERT OR UPDATE OR DELETE ON public.build_restrictions
FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();
