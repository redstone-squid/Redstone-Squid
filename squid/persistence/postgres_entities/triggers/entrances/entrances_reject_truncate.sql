CREATE TRIGGER entrances_reject_truncate BEFORE TRUNCATE ON public.entrances FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();
