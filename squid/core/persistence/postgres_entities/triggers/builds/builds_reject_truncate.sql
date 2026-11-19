CREATE TRIGGER builds_reject_truncate
    BEFORE TRUNCATE ON public.builds
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
