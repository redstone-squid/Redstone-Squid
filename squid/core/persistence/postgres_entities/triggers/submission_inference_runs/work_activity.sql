CREATE TRIGGER submission_inference_runs_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.submission_inference_runs
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'id');
