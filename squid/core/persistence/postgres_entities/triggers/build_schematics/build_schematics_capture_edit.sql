CREATE TRIGGER build_schematics_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.build_schematics
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
