CREATE TRIGGER media_normalization_jobs_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.media_normalization_jobs
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'upload_id');
