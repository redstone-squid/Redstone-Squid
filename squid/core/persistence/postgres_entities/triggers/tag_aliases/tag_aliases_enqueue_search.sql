CREATE TRIGGER tag_aliases_enqueue_search
    AFTER INSERT OR DELETE OR UPDATE ON public.tag_aliases
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_metadata_search_projection();
