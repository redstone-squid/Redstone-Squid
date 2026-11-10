CREATE TRIGGER submission_drafts_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_drafts FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();
