CREATE TRIGGER builds_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.builds
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
