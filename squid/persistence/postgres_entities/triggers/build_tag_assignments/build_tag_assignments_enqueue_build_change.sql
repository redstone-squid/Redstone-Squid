CREATE TRIGGER build_tag_assignments_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
