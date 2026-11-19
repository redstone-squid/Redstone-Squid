CREATE TRIGGER extenders_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.extenders
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
