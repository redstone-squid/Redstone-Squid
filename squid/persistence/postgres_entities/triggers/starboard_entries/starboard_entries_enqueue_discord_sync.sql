CREATE TRIGGER starboard_entries_enqueue_discord_sync
    AFTER INSERT OR DELETE OR UPDATE OF score ON public.starboard_entries
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_starboard_sync();
