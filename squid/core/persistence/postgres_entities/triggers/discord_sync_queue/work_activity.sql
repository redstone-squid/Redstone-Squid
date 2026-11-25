CREATE TRIGGER discord_sync_queue_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.discord_sync_queue
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'id');
