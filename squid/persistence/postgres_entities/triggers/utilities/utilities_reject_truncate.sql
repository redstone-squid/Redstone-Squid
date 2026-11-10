CREATE TRIGGER utilities_reject_truncate BEFORE TRUNCATE ON public.utilities FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();
