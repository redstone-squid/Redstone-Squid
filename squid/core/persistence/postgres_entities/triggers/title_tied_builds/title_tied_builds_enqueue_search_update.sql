CREATE TRIGGER title_tied_builds_enqueue_search_update
    AFTER UPDATE ON public.title_tied_builds
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_record_search_projection();
