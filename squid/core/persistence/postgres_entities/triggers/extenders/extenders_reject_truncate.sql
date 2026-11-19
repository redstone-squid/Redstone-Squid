CREATE TRIGGER extenders_reject_truncate
    BEFORE TRUNCATE ON public.extenders
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
