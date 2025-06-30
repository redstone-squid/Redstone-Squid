CREATE TRIGGER build_types_refresh_smallest_door
AFTER INSERT
OR DELETE
OR
UPDATE ON public.build_types FOR EACH ROW
EXECUTE FUNCTION trg_refresh_smallest_door ();