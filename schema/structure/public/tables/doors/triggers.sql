CREATE TRIGGER doors_refresh_smallest_door
AFTER INSERT
OR DELETE
OR
UPDATE ON public.doors FOR EACH ROW
EXECUTE FUNCTION trg_refresh_smallest_door ();