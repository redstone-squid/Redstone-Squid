CREATE TRIGGER build_creators_reject_truncate
    BEFORE TRUNCATE ON public.build_creators
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
