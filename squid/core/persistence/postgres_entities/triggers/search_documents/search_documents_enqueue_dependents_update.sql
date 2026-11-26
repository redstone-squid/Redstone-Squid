CREATE TRIGGER search_documents_enqueue_dependents_update
    AFTER UPDATE ON public.search_documents
    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_dependent_record_search();
