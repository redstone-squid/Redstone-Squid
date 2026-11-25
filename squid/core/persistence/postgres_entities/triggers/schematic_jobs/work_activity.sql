CREATE TRIGGER schematic_jobs_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.schematic_jobs
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'id');
