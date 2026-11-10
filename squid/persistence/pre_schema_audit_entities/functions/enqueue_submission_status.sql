CREATE FUNCTION public.enqueue_submission_status() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    run_key uuid;
    draft_key uuid;
    target_kind text;
    target_key text;
BEGIN
    IF TG_TABLE_NAME = 'submission_inference_runs' THEN
        IF (CASE WHEN TG_OP = 'DELETE' THEN OLD.inputs ELSE NEW.inputs END)->>'purpose' != 'submission' THEN
            RETURN NULL;
        END IF;
        run_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSIF TG_TABLE_NAME = 'submission_drafts' THEN
        draft_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        run_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.inference_run_id ELSE NEW.inference_run_id END;
    ELSE
        draft_key := CASE WHEN TG_OP = 'DELETE' THEN OLD.draft_id ELSE NEW.draft_id END;
        SELECT inference_run_id INTO run_key FROM public.submission_drafts WHERE id = draft_key;
    END IF;
    FOR target_kind, target_key IN
        SELECT 'submission_draft', draft_key::text WHERE draft_key IS NOT NULL
        UNION ALL SELECT 'inference_run', run_key::text WHERE run_key IS NOT NULL
    LOOP
        INSERT INTO public.discord_sync_queue
            (resource_kind, source_key, action, generation, enqueued_at, available_at,
             claimed_at, claim_token, dead_at, attempts, last_error)
        VALUES (target_kind, target_key, 'refresh', nextval('public.discord_sync_generation_seq'),
                now(), now(), NULL, NULL, NULL, 0, NULL)
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action, generation = EXCLUDED.generation,
            enqueued_at = EXCLUDED.enqueued_at, available_at = EXCLUDED.available_at,
            claimed_at = NULL, claim_token = NULL, dead_at = NULL, attempts = 0, last_error = NULL;
    END LOOP;
    RETURN NULL;
END;
$$;
