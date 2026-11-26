CREATE TRIGGER title_holders_enqueue_search_delete
    AFTER DELETE ON public.title_holders
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_record_search_projection();
