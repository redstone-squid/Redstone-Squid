CREATE FUNCTION public.enqueue_record_taxonomy_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO public.record_recompute_queue (scope_key, build_kind, reasons)
    VALUES ('door', 'door', '["taxonomy_change"]'::jsonb),
           ('extender', 'extender', '["taxonomy_change"]'::jsonb)
    ON CONFLICT (scope_key) DO UPDATE SET
        enqueued_at = now(), available_at = now(),
        claimed_at = NULL, claim_token = NULL;
    RETURN NULL;
END;
$$;

