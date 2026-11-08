CREATE FUNCTION public.enqueue_starboard_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_key text;
BEGIN
    IF TG_TABLE_NAME = 'starboards' THEN
        -- A configuration change restyles or re-thresholds every entry on the board.
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        SELECT
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
        FROM public.starboard_entries e
        WHERE e.starboard_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
        RETURN NULL;
    END IF;

    IF TG_TABLE_NAME = 'starboard_origin_messages' THEN
        target_key := NULL;
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        SELECT
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
        FROM public.starboard_entries e
        WHERE e.origin_message_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
        RETURN NULL;
    END IF;

    IF TG_OP = 'DELETE' THEN
        target_key := OLD.starboard_id || ':' || OLD.origin_message_id;
    ELSE
        target_key := NEW.starboard_id || ':' || NEW.origin_message_id;
    END IF;
    INSERT INTO public.discord_sync_queue
        (resource_kind, source_key, action, generation, enqueued_at, available_at,
         claimed_at, claim_token, dead_at, attempts, last_error)
    VALUES (
        'starboard_entry', target_key, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'refresh' END,
        nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
    )
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action, generation = EXCLUDED.generation, enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
    RETURN NULL;
END;
$$;

