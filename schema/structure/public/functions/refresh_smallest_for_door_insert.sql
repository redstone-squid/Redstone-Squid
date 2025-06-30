CREATE
OR REPLACE PROCEDURE public.refresh_smallest_for_door_insert (IN p_build_id bigint) LANGUAGE sql AS $procedure$
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
$procedure$;