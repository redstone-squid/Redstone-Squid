CREATE TRIGGER other_builds_reject_truncate
    BEFORE TRUNCATE ON public.other_builds
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
