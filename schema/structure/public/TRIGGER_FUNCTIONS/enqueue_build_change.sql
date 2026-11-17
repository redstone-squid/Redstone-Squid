CREATE FUNCTION public.enqueue_build_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_build_id bigint;
    target_kind text;
    previous_kind text;
    build_exists boolean := true;
    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);
        IF TG_OP = 'UPDATE' THEN
            previous_kind := lower(OLD.category);
        END IF;
    ELSE
        -- When the row is going away with its build, the build's own trigger queues the deletion.
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;
        build_exists := FOUND;
    END IF;

    -- A link change queues only the Discord sync.
    IF TG_TABLE_NAME <> 'build_links' THEN
        PERFORM public.upsert_search_projection_request(
            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END
        );

        IF target_kind IN ('door', 'extender') OR previous_kind IN ('door', 'extender') THEN
            -- As in the upsert_*_request functions, clearing the claim token keeps an older worker
            -- from acknowledging the new request.
            INSERT INTO public.record_recompute_queue
                (scope_key, build_kind, build_id, reasons, enqueued_at, available_at)
            SELECT kind, kind,
                CASE WHEN build_deleted THEN NULL ELSE target_build_id END,
                '["source_change"]'::jsonb, now(), now()
            FROM (
                SELECT DISTINCT kind FROM unnest(ARRAY[target_kind, previous_kind]) AS kinds(kind)
                WHERE kind IN ('door', 'extender')
            ) AS affected
            ORDER BY kind
            ON CONFLICT (scope_key) DO UPDATE
            SET build_id = EXCLUDED.build_id,
                reasons = (
                    SELECT jsonb_agg(DISTINCT reason)
                    FROM jsonb_array_elements_text(
                        record_recompute_queue.reasons || EXCLUDED.reasons
                    ) AS reason
                ),
                enqueued_at = EXCLUDED.enqueued_at,
                available_at = EXCLUDED.available_at,
                attempts = 0,
                claimed_at = NULL,
                claim_token = NULL,
                last_error = NULL;
        END IF;
    END IF;

    IF build_deleted THEN
        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');
    ELSIF build_exists THEN
        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');

        -- A review card embeds the build it is voting on, so a build change makes the
        -- session's cards stale too. Their posts are keyed by session, not by build, so
        -- the sessions have to be enqueued in their own right.
        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')
        FROM public.build_vote_sessions bvs
        WHERE bvs.build_id = target_build_id;
    END IF;
    RETURN NULL;
END;
$$;

