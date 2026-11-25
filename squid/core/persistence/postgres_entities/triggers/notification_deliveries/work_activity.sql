CREATE TRIGGER notification_deliveries_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.notification_deliveries
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'id');
