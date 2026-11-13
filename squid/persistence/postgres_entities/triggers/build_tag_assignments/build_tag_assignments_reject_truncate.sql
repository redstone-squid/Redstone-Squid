CREATE TRIGGER build_tag_assignments_reject_truncate
    BEFORE TRUNCATE ON public.build_tag_assignments
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
