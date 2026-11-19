CREATE TRIGGER door_timing_variants_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.door_timing_variants
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
