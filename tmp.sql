CREATE MATERIALIZED VIEW public.door_search_cache AS
SELECT
    b.*,
    d.orientation,
    d.door_width,
    d.door_height,
    COALESCE(d.door_depth, 1) AS door_depth,

    /* types: must be present; no COALESCE, no default */
    COALESCE(
        ARRAY_AGG(DISTINCT t.name ORDER BY t.name)
            FILTER (WHERE t.name IS NOT NULL),
        ARRAY[]::text[]
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