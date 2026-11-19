CREATE TRIGGER build_credits_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.build_credits
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
