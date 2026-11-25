CREATE FUNCTION public.trace_work_activity(activity_id bigint) RETURNS SETOF public.work_activity
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE ancestry(id) AS (
        SELECT a.id FROM public.work_activity a WHERE a.id = activity_id
        UNION
        SELECT parent.id
        FROM ancestry
        JOIN public.work_activity a ON a.id = ancestry.id
        CROSS JOIN LATERAL (
            -- Each delivery can produce further queues or events in another process.
            SELECT lease.id FROM public.work_activity lease
            WHERE lease.acquired_claim = a.parent_claim AND lease.id < a.id
            UNION ALL
            -- A coalesced queue item may have several producers. Stop at this lifetime's insert.
            (SELECT prev.id FROM public.work_activity prev
             WHERE a.transition <> 'insert' AND prev.relation = a.relation AND prev.item = a.item
               AND prev.id < a.id AND prev.transition <> 'delete'
             ORDER BY prev.id DESC LIMIT 1)
        ) parent
    )
    SELECT a.* FROM ancestry JOIN public.work_activity a USING (id) ORDER BY a.id
    $$;
