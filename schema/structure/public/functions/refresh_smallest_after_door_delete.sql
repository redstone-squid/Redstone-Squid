CREATE
OR REPLACE PROCEDURE public.refresh_smallest_after_door_delete (IN p_build_id bigint) LANGUAGE sql AS $procedure$
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
$procedure$;