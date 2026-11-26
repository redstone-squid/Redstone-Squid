CREATE TRIGGER search_documents_enqueue_dependents_insert
    AFTER INSERT ON public.search_documents
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.enqueue_dependent_record_search();
