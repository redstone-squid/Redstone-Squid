CREATE TRIGGER build_source_messages_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.build_source_messages
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
