CREATE TRIGGER creator_aliases_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.creator_aliases FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
