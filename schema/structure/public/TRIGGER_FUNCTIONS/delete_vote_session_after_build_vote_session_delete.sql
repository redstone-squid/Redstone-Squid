CREATE FUNCTION public.delete_vote_session_after_build_vote_session_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- A build session has exactly one subtype row (vote_sessions_kind_subtype_check), so losing
    -- it, usually through the cascade from a deleted build, leaves a session nothing can show.
    -- When the delete cascaded from vote_sessions itself, the parent is already gone.
    DELETE FROM public.vote_sessions WHERE id = OLD.vote_session_id AND kind = 'build';
    RETURN NULL;
END;
$$;

