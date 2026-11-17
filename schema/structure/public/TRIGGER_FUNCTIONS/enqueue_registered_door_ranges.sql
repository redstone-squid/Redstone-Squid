CREATE FUNCTION public.enqueue_registered_door_ranges() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE public.record_input_generations SET generation = generation + 1
    WHERE build_kind = NEW.build_kind;
    IF NEW.build_kind = 'door' THEN
        INSERT INTO public.door_range_recompute_queue (query_id)
        SELECT id FROM public.door_range_registrations ORDER BY id
        ON CONFLICT (query_id) DO UPDATE SET
            enqueued_at = now(), available_at = now(),
            claimed_at = NULL, claim_token = NULL;
    END IF;
    RETURN NULL;
END;
$$;

