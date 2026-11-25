CREATE TRIGGER submission_drafts_work_activity
    AFTER UPDATE OF preparation_retry_at, preparation_attempt ON public.submission_drafts
    FOR EACH ROW WHEN (OLD.preparation_retry_at IS NOT NULL OR NEW.preparation_retry_at IS NOT NULL)
    EXECUTE FUNCTION public.record_work_activity('preparation', 'id');
