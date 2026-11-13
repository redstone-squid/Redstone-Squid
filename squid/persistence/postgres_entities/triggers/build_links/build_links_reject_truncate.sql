CREATE TRIGGER build_links_reject_truncate
    BEFORE TRUNCATE ON public.build_links
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
