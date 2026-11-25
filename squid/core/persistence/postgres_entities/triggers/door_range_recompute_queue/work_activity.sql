CREATE TRIGGER door_range_recompute_queue_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.door_range_recompute_queue
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'query_id');
