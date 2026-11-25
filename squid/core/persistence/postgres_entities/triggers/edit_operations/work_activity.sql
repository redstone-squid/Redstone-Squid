CREATE TRIGGER edit_operations_work_activity
    AFTER INSERT ON public.edit_operations
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('fact', 'id');
