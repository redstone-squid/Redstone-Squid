CREATE TRIGGER tag_definitions_enqueue_search
    AFTER INSERT OR DELETE OR UPDATE ON public.tag_definitions
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_metadata_search_projection();
