-- Queue many search documents at once, as statement-level triggers do. Keys are
-- deduplicated and locked in order, so concurrent bulk changes cannot deadlock.
CREATE FUNCTION public.upsert_search_projection_requests(
    resource_kind_input text,
    source_keys text[],
    action_input text
) RETURNS void
    LANGUAGE sql
    AS $$
    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at, available_at)
    SELECT resource_kind_input, keys.source_key, action_input, now(), now()
    FROM (SELECT DISTINCT unnest(source_keys) AS source_key) AS keys
    WHERE keys.source_key IS NOT NULL
    ORDER BY keys.source_key
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        attempts = 0,
        claimed_at = NULL,
        claim_token = NULL,
        dead_at = NULL,
        last_error = NULL;
$$;
