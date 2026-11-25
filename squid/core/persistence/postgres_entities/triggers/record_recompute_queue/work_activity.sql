CREATE TRIGGER record_recompute_queue_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.record_recompute_queue
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'id');
