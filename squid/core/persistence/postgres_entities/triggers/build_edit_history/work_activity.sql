CREATE TRIGGER build_edit_history_work_activity
    AFTER INSERT ON public.build_edit_history
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('fact', 'id');
