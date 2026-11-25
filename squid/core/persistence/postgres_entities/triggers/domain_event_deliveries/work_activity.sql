CREATE TRIGGER domain_event_deliveries_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.domain_event_deliveries
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'event_id', 'consumer');
