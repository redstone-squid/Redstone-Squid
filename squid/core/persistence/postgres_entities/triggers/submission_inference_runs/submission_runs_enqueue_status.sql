CREATE TRIGGER submission_runs_enqueue_status
    AFTER INSERT OR DELETE OR UPDATE ON public.submission_inference_runs
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_submission_status();
