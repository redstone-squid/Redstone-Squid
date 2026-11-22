CREATE TRIGGER tag_units_enqueue_records
AFTER INSERT OR UPDATE OR DELETE ON public.tag_units
FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_tag_change();
