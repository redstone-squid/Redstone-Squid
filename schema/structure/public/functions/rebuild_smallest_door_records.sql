CREATE
OR REPLACE PROCEDURE public.rebuild_smallest_door_records () LANGUAGE plpgsql AS $procedure$
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
$procedure$;