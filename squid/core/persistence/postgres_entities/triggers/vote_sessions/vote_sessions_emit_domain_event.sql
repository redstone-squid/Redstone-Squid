CREATE TRIGGER vote_sessions_emit_domain_event
    AFTER UPDATE OF status ON public.vote_sessions
    FOR EACH ROW
    EXECUTE FUNCTION public.emit_domain_event();
