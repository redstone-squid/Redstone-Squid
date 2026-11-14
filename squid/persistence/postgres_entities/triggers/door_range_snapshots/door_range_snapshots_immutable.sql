CREATE TRIGGER door_range_snapshots_immutable
BEFORE UPDATE OR DELETE ON public.door_range_snapshots
FOR EACH ROW EXECUTE FUNCTION public.reject_door_range_snapshot_mutation();
