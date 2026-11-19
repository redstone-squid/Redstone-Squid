CREATE TRIGGER build_versions_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.build_versions
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
