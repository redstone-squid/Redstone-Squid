CREATE TRIGGER build_creators_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.build_creators
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
