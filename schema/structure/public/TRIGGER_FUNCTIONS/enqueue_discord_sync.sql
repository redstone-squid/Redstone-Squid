CREATE FUNCTION public.enqueue_discord_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_kind text;
    target_key bigint;
    target_action text := 'refresh';
BEGIN
    IF TG_TABLE_NAME = 'vote_sessions' THEN
        target_kind := 'vote_session';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;
    ELSIF TG_TABLE_NAME = 'votes' THEN
        target_kind := 'vote_session';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.vote_session_id ELSE NEW.vote_session_id END;
        IF NOT EXISTS (SELECT 1 FROM public.vote_sessions WHERE id = target_key) THEN RETURN NULL; END IF;
    ELSIF TG_TABLE_NAME = 'builds' THEN
        target_kind := 'build';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN target_action := 'delete'; END IF;
    ELSE
        target_kind := 'build';
        target_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        IF NOT EXISTS (SELECT 1 FROM public.builds WHERE id = target_key) THEN RETURN NULL; END IF;
    END IF;

    -- The generation is drawn from a sequence rather than counted per row. Acknowledging
    -- a job deletes its queue row, so a per-row counter restarted at 1 on the next edit
    -- and could name a revision below one already applied. Sequences are exempt from
    -- rollback, which is exactly what a staleness token needs.
    INSERT INTO public.discord_sync_queue
        (resource_kind, source_key, action, generation, enqueued_at, available_at,
         claimed_at, claim_token, dead_at, attempts, last_error)
    VALUES (
        target_kind, target_key::text, target_action,
        nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
    )
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        generation = EXCLUDED.generation,
        enqueued_at = EXCLUDED.enqueued_at,
        available_at = EXCLUDED.available_at,
        claimed_at = NULL,
        claim_token = NULL,
        dead_at = NULL,
        attempts = 0,
        last_error = NULL;

    -- A review card embeds the build it is voting on, so a build change makes the
    -- session's cards stale too. Their posts are keyed by session, not by build, so
    -- the sessions have to be enqueued in their own right.
    IF target_kind = 'build' AND target_action = 'refresh' THEN
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        SELECT
            'vote_session', bvs.vote_session_id::text, 'refresh',
            nextval('public.discord_sync_generation_seq'), now(), now(), NULL, NULL, NULL, 0, NULL
        FROM public.build_vote_sessions bvs
        WHERE bvs.build_id = target_key
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET generation = EXCLUDED.generation,
            enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            claimed_at = NULL,
            claim_token = NULL,
            dead_at = NULL,
            attempts = 0,
            last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;

