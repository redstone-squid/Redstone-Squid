CREATE TRIGGER domain_events_work_activity
    AFTER INSERT ON public.domain_events
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('fact', 'id');
