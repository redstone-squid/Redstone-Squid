CREATE TRIGGER versions_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.versions
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
