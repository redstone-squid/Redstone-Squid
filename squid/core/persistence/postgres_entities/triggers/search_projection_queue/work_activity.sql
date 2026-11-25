CREATE TRIGGER search_projection_queue_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.search_projection_queue
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'id');
