CREATE TRIGGER record_input_snapshots_immutable
BEFORE UPDATE OR DELETE ON public.record_input_snapshots
FOR EACH ROW EXECUTE FUNCTION public.reject_record_input_snapshot_mutation();
