CREATE TRIGGER search_embedding_queue_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.search_embedding_queue
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'document_id');
