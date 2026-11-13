CREATE FUNCTION public.upsert_discord_sync_request(resource_kind_input text, source_key_input text, action_input text) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- The generation is drawn from a sequence rather than counted per row. Acknowledging
    -- a job deletes its queue row, so a per-row counter restarted at 1 on the next edit
    -- and could name a revision below one already applied. Sequences are exempt from
    -- rollback, which is exactly what a staleness token needs.
    INSERT INTO public.discord_sync_queue
        (resource_kind, source_key, action, generation, enqueued_at, available_at)
    VALUES (
        resource_kind_input, source_key_input, action_input,
        nextval('public.discord_sync_generation_seq'), now(), now()
    )
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        generation = EXCLUDED.generation,
        enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        attempts = 0,
        claimed_at = NULL,
        claim_token = NULL,
        dead_at = NULL,
        last_error = NULL;
END;
$$;

