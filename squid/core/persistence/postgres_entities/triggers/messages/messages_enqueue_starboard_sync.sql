CREATE TRIGGER messages_enqueue_starboard_sync
    AFTER UPDATE OF deleted_at ON public.messages
    FOR EACH ROW
    WHEN (OLD.deleted_at IS DISTINCT FROM NEW.deleted_at)
    EXECUTE FUNCTION public.enqueue_starboard_sync();
