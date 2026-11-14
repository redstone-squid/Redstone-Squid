CREATE TRIGGER build_credits_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.build_credits
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
