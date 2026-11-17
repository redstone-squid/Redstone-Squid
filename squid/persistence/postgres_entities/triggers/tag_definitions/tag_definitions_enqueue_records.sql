CREATE TRIGGER tag_definitions_enqueue_records
AFTER INSERT OR UPDATE OR DELETE ON public.tag_definitions
FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_taxonomy_change();
