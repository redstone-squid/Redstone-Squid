CREATE FUNCTION public.enqueue_build_source_preview_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM public.upsert_discord_sync_request('build', source.build_id::text, 'refresh')
    FROM public.build_source_messages source
    WHERE source.message_id = NEW.id
    ORDER BY source.build_id;

    PERFORM public.upsert_discord_sync_request('vote_session', review.vote_session_id::text, 'refresh')
    FROM public.build_source_messages source
    JOIN public.build_vote_sessions review ON review.build_id = source.build_id
    WHERE source.message_id = NEW.id
    ORDER BY review.vote_session_id;
    RETURN NULL;
END;
$$;

