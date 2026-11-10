CREATE TRIGGER doors_reject_truncate BEFORE TRUNCATE ON public.doors FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();
