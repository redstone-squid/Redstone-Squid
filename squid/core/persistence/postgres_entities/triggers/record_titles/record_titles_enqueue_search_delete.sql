CREATE TRIGGER record_titles_enqueue_search_delete
    AFTER DELETE ON public.record_titles
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_record_search_projection();
