CREATE TRIGGER build_schematics_reject_truncate BEFORE TRUNCATE ON public.build_schematics FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();
