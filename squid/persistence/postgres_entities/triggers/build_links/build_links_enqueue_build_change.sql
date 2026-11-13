CREATE TRIGGER build_links_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.build_links
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
