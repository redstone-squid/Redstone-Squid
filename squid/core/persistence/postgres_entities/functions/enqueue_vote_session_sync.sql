CREATE FUNCTION public.enqueue_vote_session_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_key bigint;
BEGIN
    IF TG_TABLE_NAME = 'vote_sessions' THEN
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        PERFORM public.upsert_discord_sync_request(
            'vote_session', target_key::text, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'refresh' END
        );
    ELSE
        -- When the vote is going away with its session, the session's own trigger queues the deletion.
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END;
        IF EXISTS (SELECT 1 FROM public.vote_sessions WHERE id = target_key) THEN
            PERFORM public.upsert_discord_sync_request('vote_session', target_key::text, 'refresh');
        END IF;
    END IF;
    RETURN NULL;
END;
$$;
