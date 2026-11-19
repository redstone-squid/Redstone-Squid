CREATE TRIGGER build_credits_reject_truncate
    BEFORE TRUNCATE ON public.build_credits
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
