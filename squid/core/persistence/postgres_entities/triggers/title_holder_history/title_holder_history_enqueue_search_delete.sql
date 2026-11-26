CREATE TRIGGER title_holder_history_enqueue_search_delete
    AFTER DELETE ON public.title_holder_history
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_record_search_projection();
