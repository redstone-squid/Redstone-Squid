CREATE TRIGGER builds_enqueue_build_change
    AFTER INSERT OR DELETE ON public.builds
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
