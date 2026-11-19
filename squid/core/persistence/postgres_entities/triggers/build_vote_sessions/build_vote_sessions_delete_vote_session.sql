CREATE TRIGGER build_vote_sessions_delete_vote_session
    AFTER DELETE ON public.build_vote_sessions
    FOR EACH ROW
    EXECUTE FUNCTION public.delete_vote_session_after_build_vote_session_delete();
