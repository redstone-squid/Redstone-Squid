CREATE TRIGGER title_tied_builds_enqueue_search_insert
    AFTER INSERT ON public.title_tied_builds
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_record_search_projection();
