CREATE TRIGGER versions_reject_truncate BEFORE TRUNCATE ON public.versions FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();
