CREATE TRIGGER other_builds_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.other_builds
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
