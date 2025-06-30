CREATE TRIGGER build_restrictions_refresh_smallest_door
AFTER INSERT
OR DELETE
OR
UPDATE ON public.build_restrictions FOR EACH ROW
EXECUTE FUNCTION trg_refresh_smallest_door ();