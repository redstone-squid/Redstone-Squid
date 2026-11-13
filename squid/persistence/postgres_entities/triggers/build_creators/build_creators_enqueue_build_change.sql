CREATE TRIGGER build_creators_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.build_creators
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
