CREATE TRIGGER doors_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.doors
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
