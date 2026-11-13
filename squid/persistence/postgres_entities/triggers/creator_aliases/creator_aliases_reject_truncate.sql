CREATE TRIGGER creator_aliases_reject_truncate
    BEFORE TRUNCATE ON public.creator_aliases
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
