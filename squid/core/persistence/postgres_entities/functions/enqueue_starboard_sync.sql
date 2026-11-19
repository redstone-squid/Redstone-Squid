CREATE FUNCTION public.enqueue_starboard_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_TABLE_NAME = 'starboards' THEN
        -- A configuration change restyles or re-thresholds every entry on the board.
        PERFORM public.upsert_discord_sync_request(
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh'
        )
        FROM public.starboard_entries e
        WHERE e.starboard_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSIF TG_TABLE_NAME = 'messages' THEN
        PERFORM public.upsert_discord_sync_request(
            'starboard_entry', e.starboard_id || ':' || e.origin_message_id, 'refresh'
        )
        FROM public.starboard_entries e
        WHERE e.origin_message_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM public.upsert_discord_sync_request(
            'starboard_entry', OLD.starboard_id || ':' || OLD.origin_message_id, 'delete'
        );
    ELSE
        PERFORM public.upsert_discord_sync_request(
            'starboard_entry', NEW.starboard_id || ':' || NEW.origin_message_id, 'refresh'
        );
    END IF;
    RETURN NULL;
END;
$$;
