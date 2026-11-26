CREATE TRIGGER title_holder_history_enqueue_search_insert
    AFTER INSERT ON public.title_holder_history
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_record_search_projection();
