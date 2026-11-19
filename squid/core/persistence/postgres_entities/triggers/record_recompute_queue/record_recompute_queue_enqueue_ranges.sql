CREATE TRIGGER record_recompute_queue_enqueue_ranges
AFTER INSERT OR UPDATE OF enqueued_at ON public.record_recompute_queue
FOR EACH ROW EXECUTE FUNCTION public.enqueue_registered_door_ranges();
