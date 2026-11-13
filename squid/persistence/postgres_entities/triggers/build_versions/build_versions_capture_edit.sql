CREATE TRIGGER build_versions_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.build_versions
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
