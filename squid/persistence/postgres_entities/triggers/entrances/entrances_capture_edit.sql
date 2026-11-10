CREATE TRIGGER entrances_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.entrances FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
