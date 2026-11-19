CREATE TRIGGER votes_enqueue_discord_sync
    AFTER INSERT OR DELETE OR UPDATE ON public.votes
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_vote_session_sync();
