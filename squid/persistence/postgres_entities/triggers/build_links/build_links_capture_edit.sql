CREATE TRIGGER build_links_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_links FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
