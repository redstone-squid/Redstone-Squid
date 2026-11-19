CREATE TRIGGER record_computation_runs_enqueue_search
    AFTER INSERT OR DELETE OR UPDATE OF is_active ON public.record_computation_runs
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_computed_record_search_projection();
