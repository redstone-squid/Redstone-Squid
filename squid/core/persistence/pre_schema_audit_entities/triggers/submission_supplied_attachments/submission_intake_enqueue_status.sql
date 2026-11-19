CREATE TRIGGER submission_intake_enqueue_status AFTER INSERT OR DELETE OR UPDATE ON public.submission_supplied_attachments FOR EACH ROW EXECUTE FUNCTION public.enqueue_submission_status();
