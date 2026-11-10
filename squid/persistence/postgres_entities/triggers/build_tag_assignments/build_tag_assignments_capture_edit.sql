CREATE TRIGGER build_tag_assignments_capture_edit BEFORE INSERT OR UPDATE OR DELETE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();
