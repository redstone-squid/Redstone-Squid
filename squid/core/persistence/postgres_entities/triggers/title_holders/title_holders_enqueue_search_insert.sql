CREATE TRIGGER title_holders_enqueue_search_insert
    AFTER INSERT ON public.title_holders
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_record_search_projection();
