CREATE TRIGGER utilities_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.utilities FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
