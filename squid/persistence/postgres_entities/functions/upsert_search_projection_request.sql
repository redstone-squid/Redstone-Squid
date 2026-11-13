-- Queue one search document for a refresh, or restart the request already queued for it.
-- Restarting clears the claim token so a worker that claimed the older request cannot acknowledge this one.
CREATE FUNCTION public.upsert_search_projection_request(
    resource_kind_input text,
    source_key_input text,
    action_input text
) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO public.search_projection_queue
        (resource_kind, source_key, action, enqueued_at, available_at)
    VALUES (resource_kind_input, source_key_input, action_input, now(), now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        attempts = 0,
        claimed_at = NULL,
        claim_token = NULL,
        dead_at = NULL,
        last_error = NULL;
END;
$$;
